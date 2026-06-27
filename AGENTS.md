# AGENTS.md

## Project Rules

1. Keep concerns separated. Avoid stringly typed keys in code; use enums or constants instead, and group constants in shared constant classes/files where practical.
2. Prefer object-oriented boundaries while keeping Python code natural. A file should be class-only or function-only where practical. Utility functions should live behind static utility classes when that keeps the boundary clean.
3. Keep function and variable names concise. Prefer readable short names over long descriptive phrases. Class names may stay explicit.
4. End-to-end usability comes first. Changes should be verified with runnable checks whenever feasible.

## Architecture

1. Keep the stable facade separate from reloadable service implementation.
2. Long-lived process state belongs in the facade or host layer.
3. Reloadable service files should expose stable entry classes and receive state through explicit arguments.
4. UI, protocol, or MCP entry layers must not import reloadable service internals directly.

## Git And Push Policy

1. The agent may commit and push directly to `origin/main` for this project when the user asks to commit, push, publish, or finish a completed implementation.
2. Do not ask for an extra confirmation before pushing to `main` when the intended scope is clear.
3. Before committing, inspect `git status` and stage only files that belong to the current task.
4. Never include unrelated untracked or modified files unless the user explicitly says they belong in the commit.
5. If the worktree is mixed, use explicit file paths with `git add`; do not use `git add -A`.
6. Run the relevant checks before pushing when feasible, and report any checks that could not be run.
