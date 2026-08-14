### Title
Force-delete (`git branch -D`) in `/clean_gone` skips merge-safety check, destroying unmerged commits on grep false-positive `[gone]` matches - (File: plugins/commit-commands/commands/clean_gone.md)

### Finding Description
The `clean_gone.md` command instructs the agent to run a pipeline that determines which branches to delete purely from a naive `grep '\[gone\]'` match against the full output line of `git branch -v` [1](#0-0) . That output line contains not only Git's own `[<remote>: gone]` tracking annotation but also the branch's latest commit subject text, so a commit whose subject line literally contains the substring `[gone]` (e.g. `git commit -m "fix: mark stale refs as [gone] in docs"`) produces a line that matches the grep pattern even though the branch's upstream tracking status is not actually "gone". The script extracts the branch name from field 1 via `awk '{print $1}'` and then unconditionally executes `git branch -D "$branch"` [2](#0-1) .

The critical additional flaw scoped by this question is the use of the force flag `-D` instead of the safe flag `-d`. `git branch -d` refuses to delete a branch that has commits not yet merged into its upstream or another branch, printing an error and aborting. `git branch -D` unconditionally deletes the branch ref regardless of merge status. Because the command hardcodes `-D`, the false-positive match path (an innocuous branch whose tip commit merely contains the literal text "[gone]") is never protected by Git's own merge-safety mechanism — the local branch and any commits that exist only on that branch (never pushed/merged) are irrecoverably removed from the ref namespace, and the objects become subject to eventual garbage collection.

There is no additional validation, confirmation prompt, or allowlist in the command file between the grep match and the destructive `-D` delete; the agent is instructed to execute the whole pipeline as-is.

### Impact Explanation
An unprivileged contributor who can get a commit merged/present in the shared history (e.g., via an accepted PR) with a subject line containing `[gone]` can cause any local branch whose current tip is that commit to be force-deleted the next time the victim runs `/clean_gone`, with no merge check. If that branch has unpushed/unmerged work, it results in unrecoverable local data loss (unauthorized destructive file/repo action performed by the agent), matching a "unauthorized command or file action" / repository integrity compromise impact.

### Likelihood Explanation
Requires the false-positive precondition already identified in the companion grep-injection finding (a commit subject containing the literal string `[gone]` becoming the tip of a real local branch) plus the victim actually invoking `/clean_gone`. This is a plausible normal-usage scenario: attacker-controlled commit messages routinely enter shared history through merged PRs, and once merged, that commit can become the tip of a maintainer's local feature/integration branch. No admin privileges, key leakage, or social engineering is needed beyond normal contribution flow.

### Recommendation
- Do not rely on grep-matching raw `git branch -v` text; instead derive gone-branches only from explicit tracking status via `git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads` and match on `[gone]` in the `upstream:track` field specifically, never against commit subject text.
- Replace `git branch -D` with `git branch -d` (safe delete) so Git's own merge check prevents deletion of branches with unmerged unique commits; only fall back to `-D` after an explicit secondary confirmation step or an additional programmatic check (`git cherry <upstream> <branch>` empty output) confirms no unmerged commits exist.

### Proof of Concept
Integration test plan:
1. In a temp git repo, create `origin` remote and a branch `feature-x` tracking `origin/feature-x`.
2. Add a local-only commit with `git commit -m "chore: cleanup [gone] refs"` on `feature-x` (this commit is never pushed, so it is unique/unmerged).
3. Do **not** delete `feature-x` on the remote (so Git's real tracking status is still up to date, not gone).
4. Run the exact pipeline from `clean_gone.md`.
5. Assert:
   - `git branch -v | grep '\[gone\]'` matches the `feature-x` line (false positive from commit subject).
   - After running the pipeline, `git rev-parse --verify feature-x` fails (branch deleted) even though `git branch -d feature-x` (safe delete) would have failed with "not fully merged" if attempted directly — demonstrating the `-D` flag bypassed the merge-safety guard and destroyed a unique commit.

### Citations

**File:** plugins/commit-commands/commands/clean_gone.md (L27-40)
```markdown
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
