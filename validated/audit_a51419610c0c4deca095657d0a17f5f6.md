### Title
Undocumented flag-value parsing in `scripts/gh.sh` allows repository-scope allowlist bypass - (File: scripts/gh.sh)

### Summary
`scripts/gh.sh` is a wrapper intended to let an automated/LLM-driven caller (invoked from `.claude/commands/triage-issue.md` and `.claude/commands/dedupe.md`) run only a fixed set of read-only `gh` subcommands, restricted to a single repository via `GH_REPO`. Like the Solidity `hashOrder` assembly block, the flag/value-separation logic is written in a terse, undocumented, low-level style (manual `skip_next` state machine) that silently discards the very safety check it exists to enforce, letting an attacker smuggle an arbitrary, non-allowlisted `gh` flag — including `--repo`/`-R` — past the allowlist and defeat the script's repository-scoping guarantee.

### Finding Description
The flag parser in `scripts/gh.sh` walks `"$@"` and classifies each token as either a flag or a positional argument: [1](#0-0) 

The loop only validates a token against `ALLOWED_FLAGS` when it is reached in the normal (`elif [[ "$arg" == -* ]]`) branch. However, when `skip_next` is `true` (i.e., the token is expected to be the *value* of a preceding flag such as `--state`, `--limit`, or `--label`), the token is appended to `FLAGS` unconditionally, with **no check that it isn't itself a flag**: [2](#0-1) 

Because this state machine is undocumented (no comment explains that `skip_next` bypasses the allowlist, nor that this is a deliberate trust boundary), it is easy to miss in review — exactly the auditability problem called out in the referenced report about unexplained low-level constructs discarding safety guarantees.

This lets a caller pass, e.g., `--state --repo=attacker/other-repo` (or `--label -Rattacker/other-repo`). `--state` matches `ALLOWED_FLAGS`, sets `skip_next=true`, and the following `--repo=attacker/other-repo` token is captured raw into `FLAGS` without ever passing through the `ALLOWED_FLAGS` allowlist check. The header comment states "All commands are scoped to the current repository via `GH_REPO`", but only the `search issues` branch explicitly re-asserts `--repo "$REPO"` before appending `"${FLAGS[@]}"`; `issue list` and `label list` rely solely on the `GH_REPO` environment variable with no explicit `--repo` flag, so a smuggled `--repo=...`/`-R...` in `FLAGS` is the only repo flag on the command line and fully overrides the intended scope: [3](#0-2) 

Even for `search issues`, because `gh` flag parsing takes the last occurrence of a repeated flag, an injected `--repo=...` placed after the script's own `--repo "$REPO"` in `${FLAGS[@]}` wins: [4](#0-3) 

### Impact Explanation
This script is invoked from Claude Code command workflows that process attacker-influenceable content (issue/PR bodies) for triage and dedupe automation: [5](#0-4) 

If an agent following `.claude/commands/triage-issue.md` or `.claude/commands/dedupe.md` is induced (via prompt injection embedded in issue content) to pass a crafted `--state`/`--label` value, the wrapper's core safety property — restricting `gh` operations to the single configured repository — is defeated. The `gh` token used by the automation (which may have org-wide read access) can then be used to enumerate issues/labels on other repositories the token can reach, producing unintended cross-repository information disclosure / "cross-target automation bleed" from a tool that was explicitly designed to be repo-scoped.

### Likelihood Explanation
Exploitation requires only crafting a value string for an already-allowed flag (`--state`, `--label`, `--limit`) that itself looks like another flag (e.g., `--repo=org/repo` or `-Rorg/repo`) — no shell metacharacters or command injection are needed, and the parser is deterministic and unconditional in the `skip_next` branch. The main precondition is that the caller (an LLM agent under prompt injection, or a misconfigured automation step) supplies attacker-influenced arguments to `gh.sh`. Given this script is wired into issue-triage/dedupe command workflows that read untrusted GitHub issue text, this is a realistic path, though it depends on the agent actually being steered to construct such a malformed flag value — a moderate-likelihood scenario for the described trust boundary (git automation / tool authorization).

### Recommendation
1. Validate every token added to `FLAGS`, including tokens consumed via `skip_next`, ensuring they do not begin with `-`/`--` (i.e., a flag's value must never itself look like a flag).
2. Never rely on `GH_REPO`/an earlier `--repo` alone as the sole scoping guard — explicitly append `--repo "$REPO"` **last**, or use `gh`'s explicit `-R`/`--repo` argument ordering guarantees, so a smuggled `--repo` cannot win.
3. Add comments documenting the `skip_next` state machine and the security invariant it must uphold, exactly as recommended in the referenced report (document each branch and the assumptions it relies on) so this class of allowlist bypass is caught in review.
4. Consider rewriting the parser using `getopt`/`getopts` or an explicit `case` over known flags, which is less error-prone than manual index-skipping logic.

### Proof of Concept
```bash
GITHUB_REPOSITORY=myorg/myrepo GH_TOKEN=... ./scripts/gh.sh issue list --state --repo=otherorg/secretrepo
```
Trace through the script:
- `SUB1=issue`, `SUB2=list` → passes the `CMD` allowlist check.
- Loop over remaining args `--state`, `--repo=otherorg/secretrepo`:
  - `--state` matches `ALLOWED_FLAGS`; added to `FLAGS`; since it has no `=`, and `--state` is in `FLAGS_WITH_VALUES`, `skip_next=true`.
  - `--repo=otherorg/secretrepo`: because `skip_next` is `true`, it is appended to `FLAGS` **without** the `ALLOWED_FLAGS` check [2](#0-1) .
- `POSITIONAL` is empty, so the `issue list`/`label list` branch executes: `gh issue list --state --repo=otherorg/secretrepo` [3](#0-2) .
- The resulting `gh` invocation lists issues from `otherorg/secretrepo` instead of the intended `myorg/myrepo`, bypassing the wrapper's single-repository scoping guarantee.

### Citations

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

**File:** scripts/gh.sh (L40-74)
```shellscript
# Separate flags from positional arguments
POSITIONAL=()
FLAGS=()
skip_next=false
for arg in "$@"; do
  if [[ "$skip_next" == true ]]; then
    FLAGS+=("$arg")
    skip_next=false
  elif [[ "$arg" == -* ]]; then
    flag="${arg%%=*}"
    matched=false
    for allowed in "${ALLOWED_FLAGS[@]}"; do
      if [[ "$flag" == "$allowed" ]]; then
        matched=true
        break
      fi
    done
    if [[ "$matched" == false ]]; then
      echo "Error: only --comments, --state, --limit, --label flags are allowed (e.g., ./scripts/gh.sh issue list --state open --limit 20)" >&2
      exit 1
    fi
    FLAGS+=("$arg")
    # If flag expects a value and isn't using = syntax, skip next arg
    if [[ "$arg" != *=* ]]; then
      for vflag in "${FLAGS_WITH_VALUES[@]}"; do
        if [[ "$flag" == "$vflag" ]]; then
          skip_next=true
          break
        fi
      done
    fi
  else
    POSITIONAL+=("$arg")
  fi
done
```

**File:** scripts/gh.sh (L76-83)
```shellscript
if [[ "$CMD" == "search issues" ]]; then
  QUERY="${POSITIONAL[0]:-}"
  QUERY_LOWER=$(echo "$QUERY" | tr '[:upper:]' '[:lower:]')
  if [[ "$QUERY_LOWER" == *"repo:"* || "$QUERY_LOWER" == *"org:"* || "$QUERY_LOWER" == *"user:"* ]]; then
    echo "Error: search query must not contain repo:, org:, or user: qualifiers (e.g., ./scripts/gh.sh search issues \"bug report\" --limit 10)" >&2
    exit 1
  fi
  gh "$SUB1" "$SUB2" "$QUERY" --repo "$REPO" "${FLAGS[@]}"
```

**File:** scripts/gh.sh (L90-96)
```shellscript
else
  if [[ ${#POSITIONAL[@]} -ne 0 ]]; then
    echo "Error: issue list and label list do not accept positional arguments (e.g., ./scripts/gh.sh issue list --state open, ./scripts/gh.sh label list --limit 100)" >&2
    exit 1
  fi
  gh "$SUB1" "$SUB2" "${FLAGS[@]}"
fi
```
