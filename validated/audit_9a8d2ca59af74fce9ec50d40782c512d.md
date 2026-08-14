### Title
Missing `allowed-tools` restriction in `clean_gone.md` allows inherited unscoped Bash execution - ([File: plugins/commit-commands/commands/clean_gone.md])

### Summary
`clean_gone.md` defines a slash command that performs git branch/worktree cleanup entirely via natural-language instructions and embedded bash blocks, but its YAML frontmatter contains only a `description` field with no `allowed-tools` restriction. This is inconsistent with the sibling command `commit.md` in the same plugin, which explicitly scopes execution via `allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*)`.

### Finding Description
The frontmatter of `clean_gone.md` is: [1](#0-0) 
containing only `description`, with no `allowed-tools` key. By contrast, `commit.md` in the same directory demonstrates the intended pattern of restricting tool use to specific git subcommands: [2](#0-1) 

Without an `allowed-tools` scope, Claude Code executing `/clean_gone` is not constrained to `Bash(git branch:*)`/`Bash(git worktree:*)` and instead relies solely on the natural-language instructions in the body (steps 1-3) to stay within git commands: [3](#0-2) 
Because the pipeline in step 3 parses branch names via `git branch -v | grep '\[gone\]' | sed ... | awk ...` and feeds them into a `while read branch` loop that runs `git worktree remove --force "$worktree"` and `git branch -D "$branch"`, if a git branch name or worktree path is attacker-crafted text (e.g., containing embedded natural-language instructions or shell metacharacters designed to be echoed back to the model), the model could be steered into appending or substituting a non-git command in its generated tool call, since there is no `allowed-tools` allowlist to mechanically block it.

### Impact Explanation
If exploited, this could let an attacker who controls a branch name or worktree path (e.g., via a shared fork or PR checkout) cause Claude to execute an unscoped command such as `curl`/`nc`, exfiltrating repository contents or secrets to an attacker endpoint — a real trust-boundary bypass beyond the declared `git branch`/`git worktree` scope.

### Likelihood Explanation
The precondition is narrow but realistic: the victim must run `/clean_gone` in a repository containing an attacker-influenced branch/remote/worktree name, which is plausible in shared-fork or CI checkout workflows. Feasibility further depends on whether Claude Code's runtime treats a missing `allowed-tools` as "inherit full Bash" versus some other default-deny behavior — this repo's markdown-only content does not document or enforce that runtime default, so this part of the exploit chain could not be confirmed from the codebase alone.

### Recommendation
Add `allowed-tools: Bash(git branch:*), Bash(git worktree:*)` to the frontmatter of `clean_gone.md`, matching the scoping convention already used in `commit.md`, so that Claude Code's tool-permission enforcement (if it exists) mechanically restricts execution regardless of what text appears in branch names or worktree paths.

### Proof of Concept
1. Integration test: parse `plugins/commit-commands/commands/clean_gone.md` frontmatter (YAML between the `---` markers) and assert an `allowed-tools` key exists and equals/contains `Bash(git branch:*)` and `Bash(git worktree:*)`. Current behavior: assertion fails because the key is absent (only `description` is present) — confirmed by direct file read at lines 1-3.
2. Regression test: parse `commit.md` frontmatter and confirm `allowed-tools` is present and scoped, as a positive control demonstrating the expected pattern exists elsewhere in the same plugin (lines 1-4).
3. Fuzz/invariant test (requires live Claude Code runtime, not verifiable from this repo alone): create a git branch named to include adversarial instruction text plus `[gone]` marker, invoke `/clean_gone`, and assert no command outside `git branch`/`git worktree` subcommands is executed. This step could not be validated against the current codebase since no runtime enforcement code for `allowed-tools` is present in this repository.

### Citations

**File:** plugins/commit-commands/commands/clean_gone.md (L1-3)
```markdown
---
description: Cleans up all git branches marked as [gone] (branches that have been deleted on the remote but still exist locally), including removing associated worktrees.
---
```

**File:** plugins/commit-commands/commands/clean_gone.md (L9-41)
```markdown
## Commands to Execute

1. **First, list branches to identify any with [gone] status**
   Execute this command:
   ```bash
   git branch -v
   ```
   
   Note: Branches with a '+' prefix have associated worktrees and must have their worktrees removed before deletion.

2. **Next, identify worktrees that need to be removed for [gone] branches**
   Execute this command:
   ```bash
   git worktree list
   ```

3. **Finally, remove worktrees and delete [gone] branches (handles both regular and worktree branches)**
   Execute this command:
   ```bash
   # Process all [gone] branches, removing '+' prefix if present
   git branch -v | grep '\[gone\]' | sed 's/^[+* ]//' | awk '{print $1}' | while read branch; do
     echo "Processing branch: $branch"
     # Find and remove worktree if it exists
     worktree=$(git worktree list | grep "\\[$branch\\]" | awk '{print $1}')
     if [ ! -z "$worktree" ] && [ "$worktree" != "$(git rev-parse --show-toplevel)" ]; then
       echo "  Removing worktree: $worktree"
       git worktree remove --force "$worktree"
     fi
     # Delete the branch
     echo "  Deleting branch: $branch"
     git branch -D "$branch"
   done
   ```
```

**File:** plugins/commit-commands/commands/commit.md (L1-4)
```markdown
---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*)
description: Create a git commit
---
```
