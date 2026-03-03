"""GitHub Projects V2 poller via gh CLI + GraphQL."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from .models import Issue

log = logging.getLogger(__name__)


class PollError(Exception):
    pass


_ITEMS_FRAGMENT = """\
      items(first: 100, after: $cursor) {
        nodes {
          id
          content {
            __typename
            ... on Issue {
              databaseId
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
        pageInfo {
          hasNextPage
          endCursor
        }
      }\
"""

USER_PROJECT_ITEMS_QUERY = f"""\
query($owner: String!, $number: Int!, $cursor: String) {{
  user(login: $owner) {{
    projectV2(number: $number) {{
{_ITEMS_FRAGMENT}
    }}
  }}
}}
"""

ORG_PROJECT_ITEMS_QUERY = f"""\
query($owner: String!, $number: Int!, $cursor: String) {{
  organization(login: $owner) {{
    projectV2(number: $number) {{
{_ITEMS_FRAGMENT}
    }}
  }}
}}
"""

# Maps owner_type key -> (query, graphql_response_key).
_OWNER_CONFIGS: dict[Literal["user", "org"], tuple[str, str]] = {
    "user": (USER_PROJECT_ITEMS_QUERY, "user"),
    "org":  (ORG_PROJECT_ITEMS_QUERY, "organization"),
}

# Maps (owner, project_number) -> "user" | "org".
# Cached for the process lifetime to avoid redundant queries.
_owner_type_cache: dict[tuple[str, int], Literal["user", "org"]] = {}


async def _run_query(
    query: str,
    owner: str,
    project_number: int,
    *,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Run a GraphQL query via `gh api graphql` and return the parsed JSON."""
    args = [
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-f", f"owner={owner}",
        "-F", f"number={project_number}",
    ]
    # cursor is omitted when None: gh sends null for the nullable $cursor: String
    # variable, which GraphQL treats as `after: null` (start from the first page).
    if cursor is not None:
        args += ["-f", f"cursor={cursor}"]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise PollError(f"gh api graphql failed: {stderr.decode().strip()}")

    return json.loads(stdout)


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
            id=content["databaseId"],
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
    """Poll a GitHub Projects V2 board for issues in the target column.

    Auto-detects whether the project belongs to a user or organization:
    tries the user query first; falls back to the org query if null.
    The result is cached per (owner, project_number) for the process lifetime.
    """
    cache_key = (owner, project_number)
    cached_type = _owner_type_cache.get(cache_key)
    if cached_type:
        other = "org" if cached_type == "user" else "user"
        types_to_try: list[Literal["user", "org"]] = [cached_type, other]
    else:
        types_to_try = ["user", "org"]

    for owner_type in types_to_try:
        query, response_key = _OWNER_CONFIGS[owner_type]
        response = await _run_query(query, owner, project_number)
        project_data = (
            (response.get("data") or {}).get(response_key) or {}
        ).get("projectV2") or {}

        if project_data:
            _owner_type_cache[cache_key] = owner_type
            return parse_project_items(project_data, target_column)

    log.warning("No project data for %s/%s (tried user and org)", owner, project_number)
    return []
