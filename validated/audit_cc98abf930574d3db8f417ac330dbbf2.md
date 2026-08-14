### Title
Flag-value blind consumption in `skip_next` logic allows smuggling unauthorized flags (e.g. `--repo=<other>`) past the allowlist - (File: scripts/gh.sh)

### Summary
`scripts/gh.sh`'s argument-parsing loop unconditionally appends the argument immediately following a `FLAGS_WITH_VALUES` flag (`--state`, `--limit`, `--label`) to `FLAGS` without re-validating it against `ALLOWED_FLAGS`. An attacker who can influence the arguments passed to `gh.sh` (e.g. via prompt injection in an issue/PR body consumed by the `triage-issue` or `dedupe` slash-command agents) can supply a value that is itself another flag such as `--repo=<attacker-controlled-repo>`, which is smuggled into the final `gh` invocation unchecked.

### Finding Description
The flag/positional-argument separation loop is: [1](#0-0) 

When `skip_next` is `true` (set after encountering `--state`, `--limit`, or `--label` without `=`), the very next token is pushed into `FLAGS` with no allowlist check at all:
```
if [[ "$skip_next" == true ]]; then
  FLAGS+=("$arg")
  skip_next=false
```
This means any string — including one that begins with `--` and is itself a disallowed flag/value, e.g. `--repo=attacker/other-repo` — is accepted as the "value" of the preceding flag and forwarded verbatim to the real `gh` invocation.

The script's entire security model rests on (a) an `ALLOWED_FLAGS` allowlist and (b) enforced repo scoping via the exported `GH_REPO`/`GITHUB_REPOSITORY` env var: [2](#0-1) 

For `search issues`, the script even explicitly tries to hard-pin the repo: [3](#0-2) 

But since a smuggled `--repo=<other>` ends up appended to `FLAGS`, which is placed *after* the script's own `--repo "$REPO"` on the `gh` command line, the last occurrence of `--repo` wins in `gh`'s CLI flag parsing, overriding the script's intended repo restriction. For `issue view`, `issue list`, and `label list`, there is no explicit `--repo` on the command line at all — scoping relies solely on the `GH_REPO` env var — and an injected `--repo=<other>` CLI flag takes precedence over that env var in `gh`.

The attacker-reachable path: the `triage-issue` and `dedupe` slash commands both restrict `Bash` tool usage to `Bash(./scripts/gh.sh:*)` and instruct the agent to call `./scripts/gh.sh issue view ...`, `issue list ...`, `search issues ...`, etc., using content taken from issue bodies/comments: [4](#0-3) [5](#0-4) 

If an attacker plants prompt-injection text in an issue/PR (a fully attacker-controlled, unprivileged surface) instructing the agent to run e.g. `./scripts/gh.sh issue list --state --repo=some-private-org/some-private-repo`, the wrapper's flag-value blind-consumption bug lets that malformed invocation pass validation and reach `gh` with the smuggled `--repo` flag intact, defeating the repo-scoping guard that `gh.sh` was specifically designed to enforce.

### Impact Explanation
This breaks the trust boundary that `gh.sh` is meant to enforce: automation intended to only read/query a single designated repository can be redirected, via prompt injection plus this parsing flaw, to read issues/labels/search results from an arbitrary other repository accessible to the underlying `gh` credential (e.g. a private repo the CI token has read access to, via org-wide scopes). This is an unauthorized data-access / trust-boundary-bypass condition (repo-scoping bypass) reachable purely from attacker-controlled issue/PR text, matching the "trust boundary bypass" / "unauthorized tool action" bounty category. It does not on its own grant code execution or write access, since `gh.sh` only permits read-only subcommands (`issue view`, `issue list`, `search issues`, `label list`).

### Likelihood Explanation
Preconditions: an attacker needs (1) the ability to post/edit an issue or PR comment (available to any unprivileged GitHub user) containing prompt-injection instructions that get processed by the `triage-issue`/`dedupe` agent flows, and (2) the agent must be induced to emit the crafted `gh.sh` argument sequence. Because the exploit only requires normal, unprivileged interaction with public issue trackers where these automations run, and the parsing bug itself is deterministic and always reachable whenever a `FLAGS_WITH_VALUES` flag is followed by any other token, this is realistically repeatable once the injection is successful.

### Recommendation
In the `skip_next` branch, validate that the consumed value does not itself look like a flag (reject or treat literally any token beginning with `-`/`--` unless intentionally allowed), e.g.:
```bash
if [[ "$skip_next" == true ]]; then
  if [[ "$arg" == -* ]]; then
    echo "Error: flag value must not start with '-'" >&2
    exit 1
  fi
  FLAGS+=("$arg")
  skip_next=false
```
Additionally, explicitly reject any `--repo`/`--repo=*` token anywhere in `FLAGS`/positional processing (defense in depth), and always pass the script-enforced `--repo "$REPO"` last (or use `gh ... --repo "$REPO"` after `"${FLAGS[@]}"` in every branch, not just `search issues`) so a smuggled duplicate cannot win by ordering.

### Proof of Concept
Integration test plan (bash):
1. Create a fake `gh` binary earlier in `PATH` that simply prints its received `argv` (one per line) instead of calling GitHub.
2. Set `GITHUB_REPOSITORY=intended-org/intended-repo`.
3. Run: `./scripts/gh.sh issue list --state --repo=attacker-org/private-repo`
4. Assert the script does **not** exit with the "only --comments, --state, --limit, --label flags are allowed" error (i.e., it does not reject `--repo=attacker-org/private-repo`).
5. Assert the fake `gh`'s captured argv contains `--repo=attacker-org/private-repo` as a literal flag element (not just a value string), confirming it was smuggled into the real invocation and would override/compete with the intended `GH_REPO`/`--repo intended-org/intended-repo` scoping.
6. Repeat for `search issues "query" --limit --repo=attacker-org/private-repo` and confirm the smuggled `--repo` appears after the script's own `--repo "$REPO"` in argv order, demonstrating it would take precedence in `gh`'s flag parser.

### Citations

**File:** scripts/gh.sh (L14-24)
```shellscript
export GH_HOST=github.com

REPO="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
if [[ -z "$REPO" || "$REPO" == */*/* || "$REPO" != */* ]]; then
  echo "Error: GH_REPO or GITHUB_REPOSITORY must be set to owner/repo format (e.g., GITHUB_REPOSITORY=anthropics/claude-code)" >&2
  exit 1
fi
export GH_REPO="$REPO"

ALLOWED_FLAGS=(--comments --state --limit --label)
FLAGS_WITH_VALUES=(--state --limit --label)
```

**File:** scripts/gh.sh (L43-74)
```shellscript
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

**File:** .claude/commands/triage-issue.md (L1-23)
```markdown
---
allowed-tools: Bash(./scripts/gh.sh:*),Bash(./scripts/edit-issue-labels.sh:*)
description: Triage GitHub issues by analyzing and applying labels
---

You're an issue triage assistant. Analyze the issue and manage labels.

IMPORTANT: Don't post any comments or messages to the issue. Your only actions are adding or removing labels.

Context:

$ARGUMENTS

TOOLS:
- `./scripts/gh.sh` — wrapper for `gh` CLI. Only supports these subcommands and flags:
  - `./scripts/gh.sh label list` — fetch all available labels
  - `./scripts/gh.sh label list --limit 100` — fetch with limit
  - `./scripts/gh.sh issue view 123` — read issue title, body, and labels
  - `./scripts/gh.sh issue view 123 --comments` — read the conversation
  - `./scripts/gh.sh issue list --state open --limit 20` — list issues
  - `./scripts/gh.sh search issues "query"` — find similar or duplicate issues
  - `./scripts/gh.sh search issues "query" --limit 10` — search with limit
- `./scripts/edit-issue-labels.sh --add-label LABEL --remove-label LABEL` — add or remove labels (issue number is read from the workflow event)
```

**File:** .claude/commands/dedupe.md (L1-27)
```markdown
---
allowed-tools: Bash(./scripts/gh.sh:*), Bash(./scripts/comment-on-duplicates.sh:*)
description: Find duplicate GitHub issues
---

Find up to 3 likely duplicate issues for a given GitHub issue.

To do this, follow these steps precisely:

1. Use an agent to check if the Github issue (a) is closed, (b) does not need to be deduped (eg. because it is broad product feedback without a specific solution, or positive feedback), or (c) already has a duplicates comment that you made earlier. If so, do not proceed.
2. Use an agent to view a Github issue, and ask the agent to return a summary of the issue
3. Then, launch 5 parallel agents to search Github for duplicates of this issue, using diverse keywords and search approaches, using the summary from #1
4. Next, feed the results from #1 and #2 into another agent, so that it can filter out false positives, that are likely not actually duplicates of the original issue. If there are no duplicates remaining, do not proceed.
5. Finally, use the comment script to post duplicates:
   ```
   ./scripts/comment-on-duplicates.sh --potential-duplicates <dup1> <dup2> <dup3>
   ```

Notes (be sure to tell this to your agents, too):

- Use `./scripts/gh.sh` to interact with Github, rather than web fetch or raw `gh`. Examples:
  - `./scripts/gh.sh issue view 123` — view an issue
  - `./scripts/gh.sh issue view 123 --comments` — view with comments
  - `./scripts/gh.sh issue list --state open --limit 20` — list issues
  - `./scripts/gh.sh search issues "query" --limit 10` — search for issues
- Do not use other tools, beyond `./scripts/gh.sh` and the comment script (eg. don't use other MCP servers, file edit, etc.)
- Make a todo list first
```
