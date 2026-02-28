"""GitHub Projects V2 poller via gh CLI + GraphQL."""

from __future__ import annotations

import asyncio
import json
import logging

from .models import Issue

log = logging.getLogger(__name__)


class PollError(Exception):
    pass


PROJECT_ITEMS_QUERY = """\
query($owner: String!, $number: Int!) {
  user(login: $owner) {
    projectV2(number: $number) {
      items(first: 100) {
        nodes {
          id
          content {
            __typename
            ... on Issue {
              number
              title
              body
              labels(first: 10) { nodes { name } }
              repository { nameWithOwner }
            }
          }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""


def parse_project_items(data: dict, target_column: str) -> list[Issue]:
    """Parse GraphQL response into Issue list, filtering by column name."""
    issues = []
    nodes = data.get("items", {}).get("nodes", [])

    for item in nodes:
        content = item.get("content")
        if not content or content.get("__typename") != "Issue":
            continue

        field_value = item.get("fieldValueByName")
        column_name = field_value.get("name", "") if field_value else ""
        if column_name != target_column:
            continue

        labels = [
            n["name"]
            for n in content.get("labels", {}).get("nodes", [])
        ]

        issues.append(Issue(
            id=0,
            number=content["number"],
            repo=content["repository"]["nameWithOwner"],
            title=content["title"],
            body=content.get("body", ""),
            labels=labels,
            project_item_id=item["id"],
        ))

    return issues


async def poll_project_issues(
    owner: str,
    project_number: int,
    target_column: str,
) -> list[Issue]:
    """Poll a GitHub Projects V2 board for issues in the target column."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "api", "graphql",
        "-f", f"query={PROJECT_ITEMS_QUERY}",
        "-f", f"owner={owner}",
        "-F", f"number={project_number}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise PollError(f"gh api graphql failed: {stderr.decode().strip()}")

    response = json.loads(stdout)
    project_data = (
        response.get("data", {})
        .get("user", {})
        .get("projectV2", {})
    )

    if not project_data:
        log.warning(f"No project data for {owner}/{project_number}")
        return []

    return parse_project_items(project_data, target_column)
