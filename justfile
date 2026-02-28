# Auto Dev Loop — development commands

default:
    @just --list

# Run all tests
test *args:
    uv run python -m pytest {{args}}

# Run tests with verbose output
test-v *args:
    uv run python -m pytest -v {{args}}

# Run a specific test file
test-file file *args:
    uv run python -m pytest {{file}} -v {{args}}

# Run the daemon
run *args:
    uv run adl {{args}}

# Install dependencies
install:
    uv sync --all-extras

# Add a dependency
add *args:
    uv add {{args}}

# Add a dev dependency
add-dev *args:
    uv add --dev {{args}}
