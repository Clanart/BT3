### Title
Renamed working-directory files diff index-vs-worktree instead of HEAD-vs-worktree, silently hiding staged content changes from review before commit - ([File: app/src/lib/git/diff.ts])

### Summary
`getWorkingDirectoryDiff` in `app/src/lib/git/diff.ts` computes the diff shown to the user for a file marked `AppFileStatusKind.Renamed` by running `git diff -- <path>` (index vs. working tree) instead of `git diff HEAD -- <path>` (HEAD vs. working tree), which is what is used for every other status kind. [1](#0-0) 
The code comment on this branch explicitly acknowledges the flaw: diffing against the index "won't show any changes already staged to the renamed file which differs from our other diffs." [2](#0-1) 
This is the "surplus accounting flaw" analog: the invariant that should hold (diff preview == what will actually be committed) is broken specifically for renames, and the un-accounted-for "surplus" is any content difference that is already staged before the user opens the Changes view.

### Finding Description
For every other file status kind, Desktop diffs explicitly against `HEAD`: [3](#0-2) 
But for `AppFileStatusKind.Renamed`, the code intentionally diffs the default range (index vs. working tree) with no `HEAD` argument: [2](#0-1) 
`git status`/`getStatus` already recognizes that a rename can carry hidden modifications and sets `renameIncludesModifications` on `AppFileStatus` when the working tree portion is `Modified` or the rename/copy score is below 100: [4](#0-3) 
However, `renameIncludesModifications` only reflects the difference between the *index* and *worktree* (the same comparison the flawed diff already performs) — it does not detect the case where content differing from `HEAD` has already been fully staged (index == worktree), which is exactly the scenario the code comment warns about. In that case `renameIncludesModifications` is `false`, no UI warning is shown, and `getWorkingDirectoryDiff` returns an empty (or minimal) diff even though the committed blob will differ substantially from `HEAD`.

This state (rename + content change already staged, with index matching worktree) is not a contrived edge case requiring local/admin access — it is the exact state Git itself produces automatically during a `merge`, `rebase`, or `cherry-pick` that involves a rename+content-change detected via Git's rename-follow heuristics. When a user pulls or merges a branch from an attacker-controlled remote/fork (a normal, expected Desktop workflow — no admin rights, no leaked credentials, no local file tampering required), Git can auto-resolve a rename with modified content and stage the merged result directly into the index without any explicit `git add` by the user. Desktop's Changes list will show the file as `Renamed` with `renameIncludesModifications` false or minimally noted, and the diff view driven by `getWorkingDirectoryDiff` will render little or nothing, because it's comparing index to worktree (both already equal) rather than HEAD to worktree.

### Impact Explanation
This results in silent corruption of what the user commits/pushes: the user reviews an empty or misleadingly small diff for a renamed file, believes no meaningful content changed, and commits/pushes a merge that actually contains attacker-supplied content changes they never saw. This falls squarely under the in-scope impact "silent corruption of what the user commits or pushes," triggered purely by merging/pulling from an attacker-controlled repository or remote — no privileged access, no malware, no social-engineering trickery beyond a routine merge/pull that a collaborator would normally perform.

### Likelihood Explanation
Likelihood is realistic but requires a specific combination: (1) the user must pull/merge a branch from a repository the attacker controls or contributes to, and (2) that branch must contain a commit renaming a file while also modifying its content, structured so Git's rename detection stages it directly (a common occurrence in real-world merges, not an exotic corner case). No further user action beyond a normal merge/pull and commit/push is needed, and the flawed comparison logic in `getWorkingDirectoryDiff` guarantees the diff preview under-reports the change every time this rename+stage pattern occurs.

### Recommendation
For `AppFileStatusKind.Renamed` (and `Copied`) files in `getWorkingDirectoryDiff`, always diff against `HEAD` (as done for `Modified`/`Deleted`/default paths) rather than against the index, so the working-directory diff view always reflects the true difference between what's committed today and what will be committed next. If diffing against the index is still needed for staged-vs-worktree granularity, surface both comparisons and clearly flag any content difference between `HEAD` and the eventual commit for renamed files, rather than only checking index-vs-worktree via `renameIncludesModifications`.

### Proof of Concept
1. Attacker prepares a branch: rename `README.md` to `install.sh` and inject a malicious script body in the same commit (a rename with score < 100 due to added lines, or with further modification after the rename).
2. Victim, using GitHub Desktop, fetches and merges/pulls this branch into their local branch. Git auto-detects the rename and stages the renamed+modified file directly (index matches working tree, and differs from `HEAD`).
3. Desktop's Changes view lists `install.sh` as `Renamed`. The diff pane calls `getWorkingDirectoryDiff`, which for `Renamed` files runs `git diff -- install.sh` (index vs. worktree) — with index and worktree identical, this returns an empty/near-empty diff, per [2](#0-1) .
4. The victim, seeing no substantive diff, commits the merge and pushes, believing only a rename occurred — while the actual committed blob for `install.sh` (relative to the previous `HEAD`) contains the attacker's injected content, which was never rendered for review.

### Citations

**File:** app/src/lib/git/diff.ts (L379-390)
```typescript
  } else if (file.status.kind === AppFileStatusKind.Renamed) {
    // NB: Technically this is incorrect, the best kind of incorrect.
    // In order to show exactly what will end up in the commit we should
    // perform a diff between the new file and the old file as it appears
    // in HEAD. By diffing against the index we won't show any changes
    // already staged to the renamed file which differs from our other diffs.
    // The closest I got to that was running hash-object and then using
    // git diff <blob> <blob> but that seems a bit excessive.
    args.push('--', ensureRelativePath(file.path))
  } else {
    args.push('HEAD', '--', ensureRelativePath(file.path))
  }
```

**File:** app/src/lib/git/status.ts (L160-169)
```typescript
  } else if (entry.kind === 'renamed' && oldPath != null) {
    return {
      kind: AppFileStatusKind.Renamed,
      oldPath,
      submoduleStatus: entry.submoduleStatus,
      renameIncludesModifications:
        entry.workingTree === GitStatusEntry.Modified ||
        (entry.renameOrCopyScore !== undefined &&
          entry.renameOrCopyScore < 100),
    }
```
