### Title
`popStashEntry()` heuristic drops the stash on unconfirmed success, causing silent loss of stashed changes - (File: `app/src/lib/git/stash.ts`)

### Summary
GitHub Desktop's stash-restore path infers success/failure of `git stash pop` from exit code and stderr length instead of verifying that the stash was actually re-applied. When `git stash pop --quiet` exits with code `1` and an empty `stderr`, the code assumes the pop "succeeded but wasn't dropped," and proceeds to permanently delete the stash entry via `dropDesktopStashEntry()`. [1](#0-0) 

### Finding Description
`popStashEntry()` wraps `git stash pop` in a `.catch()` handler that treats any non-`MergeConflicts` failure with `exitCode === 1 && stderr.length === 0` as a "successful pop that Git failed to report correctly," and reacts by calling `dropDesktopStashEntry(repository, stashSha)`, which runs `git stash drop <name>` unconditionally: [2](#0-1) [3](#0-2) 

This is the same broken invariant as the ERC20 report: the code equates "no explicit failure signal on the channel it inspects" with "the operation succeeded," when in fact Git's own contributors already flagged this heuristic as unreliable — the sibling function `createDesktopStashEntry()` contains an explicit comment ("Here be dragons…") documenting that the assumption "exit code 1 + no `error:` line ⇒ operation actually succeeded" does *not* hold for all cases, citing the unborn-repository case where `git stash push` exits 1 with a message that is not prefixed `error:` yet no stash is created: [4](#0-3) 

`popStashEntry()` never verifies via `git stash list`, working-directory status, or the stash SHA that the changes were truly restored before calling `dropDesktopStashEntry`. Any Git failure mode that (a) exits with code `1`, (b) writes nothing to `stderr` (e.g. because the message went to `stdout`, was suppressed by `--quiet`, or was produced by content-driven machinery such as a clean/smudge filter or merge driver failing without stderr output), and (c) does not actually restore the stash, will be misclassified as "popped successfully," and Desktop will delete the only copy of the user's stashed changes.

### Impact Explanation
This falls squarely in the requested impact class of "silent corruption of what the user commits or pushes": the user's uncommitted work, safely preserved in the stash, is irreversibly deleted (`git stash drop`) based on an incorrect success inference, with no working-directory changes actually restored. There is no confirmation step, no diff/status check, and no undo — the data is gone once `dropDesktopStashEntry` runs.

### Likelihood Explanation
The exact trigger conditions (exit code 1, empty stderr, pop not actually applied) are narrow and Git-version/version-behavior dependent, and the surrounding code comments themselves admit this is "not the greatest approach" because "stash isn't very communicative." I was not able to fully verify, within the available tool budget, a concrete attacker-controlled repository artifact (e.g., a specific `.gitattributes` filter/merge driver or repository state deliverable purely via clone/fetch) that reliably forces this exact exit-code/stderr combination while leaving the stash unapplied — this would require deeper investigation of Git's stash-pop internals and how `--quiet` affects error routing, which I could not complete before this iteration limit. I flag this uncertainty explicitly rather than asserting a fully proven exploit chain.

### Recommendation
Do not infer stash success from exit code/stderr shape. After a `git stash pop` failure, explicitly verify whether the stash was applied (e.g., re-run `git stash list`/compare `refs/stash` before and after, or check working-directory status for the expected changes) before calling `dropDesktopStashEntry`. If the state cannot be positively confirmed, leave the stash entry intact and surface the ambiguity to the user rather than silently dropping data.

### Proof of Concept
Conceptual reproduction path (not fully verified due to tool-call limits):
1. Attacker delivers/controls a repository state (e.g. via a crafted merge scenario or filter configuration reachable through cloned repo content) such that, when the victim's Desktop client calls `git stash pop --quiet <name>` on it, Git exits with code `1` while emitting nothing on `stderr`.
2. `popStashEntry()`'s catch handler in `app/src/lib/git/stash.ts` matches this condition and calls `dropDesktopStashEntry(repository, stashSha)`. [5](#0-4) 
3. `dropDesktopStashEntry` runs `git stash drop <name>` unconditionally, permanently deleting the stash entry. [2](#0-1) 
4. The victim's uncommitted/stashed changes are lost with no error surfaced, matching the "silent corruption of what the user commits" impact class.

### Citations

**File:** app/src/lib/git/stash.ts (L161-199)
```typescript
  const result = await git(args, repository.path, 'createStashEntry').catch(
    e => {
      // Note: 2024: Here be dragons. As I converted this code to get rid of the
      // successExitCode use I got curious about the assumptions made in the
      // following logic. It assumes that as long as the exit code for `git
      // stash push` is 1 and there are no lines beginning with "error: " then
      // a stash was created. That didn't hold up to a quick read of the stash
      // code. For example, running git stash push in an unborn repository will
      // get you an exit code of 1 but no stash was created:
      //
      // % git stash push -m foo ; echo $?
      // You do not have the initial commit yet
      // 1
      //
      // I'm not going to mess with this now but I felt the need to document
      // my findings should I or any other brave soul choose to tackle this in
      // the future.
      if (e instanceof GitError && e.result.exitCode === 1) {
        // search for any line starting with `error:` -  /m here to ensure this is
        // applied to each line, without needing to split the text
        const errorPrefixRe = /^error: /m

        const matches = errorPrefixRe.exec(coerceToString(e.result.stderr))
        if (matches !== null && matches.length > 0) {
          // rethrow, because these messages should prevent the stash from being created
          return Promise.reject(e)
        }

        // if no error messages were emitted by Git, we should log but continue because
        // a valid stash was created and this should not interfere with the checkout

        log.info(
          `[createDesktopStashEntry] a stash was created successfully but exit code ${result.exitCode} reported. stderr: ${result.stderr}`
        )
        return e.result
      }
      return Promise.reject(e)
    }
  )
```

**File:** app/src/lib/git/stash.ts (L219-229)
```typescript
export async function dropDesktopStashEntry(
  repository: Repository,
  stashSha: string
) {
  const entryToDelete = await getStashEntryMatchingSha(repository, stashSha)

  if (entryToDelete !== null) {
    const args = ['stash', 'drop', entryToDelete.name]
    await git(args, repository.path, 'dropStashEntry')
  }
}
```

**File:** app/src/lib/git/stash.ts (L245-269)
```typescript
  const stashToPop = await getStashEntryMatchingSha(repository, stashSha)

  if (stashToPop !== null) {
    const args = ['stash', 'pop', '--quiet', `${stashToPop.name}`]
    await git(args, repository.path, 'popStashEntry', {
      expectedErrors,
    }).catch(e => {
      // popping a stashes that create conflicts in the working directory
      // report an exit code of `1` and are not dropped after being applied.
      // so, we check for this case and drop them manually unless there's
      // anything in stderr as that could have prevented the stash from being
      // popped. Not the greatest approach but stash isn't very communicative
      if (
        e instanceof GitError &&
        e.result.exitCode === 1 &&
        e.result.stderr.length === 0
      ) {
        log.info(
          `[popStashEntry] a stash was popped successfully but exit code ${e.result.exitCode} reported.`
        )
        // bye bye
        return dropDesktopStashEntry(repository, stashSha)
      }
      return Promise.reject(e)
    })
```
