## Finding: Heuristic-based stash-pop success detection can silently destroy uncommitted work

The reported bug class is: a git-related operation returns an ambiguous/non-reverting status instead of a hard success/failure signal, the caller does not properly validate it, and the app proceeds as if the operation succeeded — corrupting the invariant between "what the user believes happened" and "what actually happened." The closest local analog in GitHub Desktop is in the stash-pop path.

### Title
Unvalidated heuristic on `git stash pop` exit code causes permanent loss of stashed changes - (File: `app/src/lib/git/stash.ts`)

### Summary
`popStashEntry` infers a successful stash-pop purely from the combination of `exitCode === 1` and an *empty* `stderr`, then unconditionally deletes ("drops") the underlying stash entry based on that inference — without ever confirming that the working directory actually received the stashed changes.

### Finding Description
`popStashEntry` calls `git stash pop` with only `MergeConflicts` as an expected error. Any other non-zero exit is caught and reinterpreted: [1](#0-0) 

```
await git(args, repository.path, 'popStashEntry', {
  expectedErrors,
}).catch(e => {
  if (
    e instanceof GitError &&
    e.result.exitCode === 1 &&
    e.result.stderr.length === 0
  ) {
    log.info(`... a stash was popped successfully but exit code ${e.result.exitCode} reported.`)
    return dropDesktopStashEntry(repository, stashSha)
  }
  return Promise.reject(e)
})
```

The comment in the sibling function `createDesktopStashEntry` explicitly documents that this class of exit-code/stderr heuristics is unreliable and was adopted without full verification of every underlying Git code path: [2](#0-1) 

The exact same fragile pattern is reused in `popStashEntry`, but with materially higher stakes: instead of just returning a boolean, it triggers `dropDesktopStashEntry`, an irreversible delete of the stash ref. Any Git behavior that produces `exitCode === 1` with empty `stderr` for a reason *other than* "pop succeeded but drop failed" (e.g., a `post-checkout`/other hook silently short-circuiting, a permissions/locking failure that Git reports without writing to `stderr`, or future Git versions changing this specific messaging behavior) will be misclassified as success. The stash is then deleted while the user's changes were never actually restored to the working directory — an unbounded, silent loss of the user's uncommitted work, and no exception is surfaced to the user or to `gitStore.performFailableOperation`'s error-emission path.

This exactly mirrors the report's core defect: **trusting an ambiguous non-reverting signal as "success" without positively confirming the state change actually occurred**, then taking an unrecoverable follow-on action based on that unchecked assumption.

### Impact Explanation
If the heuristic misfires, `dropDesktopStashEntry` permanently deletes the user's Desktop-created stash entry while their changes are not actually present in the working directory, resulting in **silent, irrecoverable loss of the user's uncommitted work** — squarely inside the "silent corruption of what the user commits" impact category, since the app's UI will show no error and the changes the user believed were safely stashed vanish.

### Likelihood Explanation
This code path executes on every stash-pop performed via Desktop's UI (e.g., switching branches with a Desktop-created stash present). The heuristic's failure mode does not require attacker-crafted content per se; it can be triggered by ordinary Git behavior changes, hook-driven repositories, or race conditions on the local filesystem/lock files — situations plausible in a repository the user has cloned or is interacting with, since the exact Git messaging on stdout/stderr for this scenario is explicitly called out in-repo as unverified ("Here be dragons").

### Recommendation
Do not infer success from `exitCode === 1 && stderr.length === 0`. Instead, after a non-conflict, non-zero exit from `git stash pop`, explicitly re-check whether the stash entry still exists (`git stash list`) and whether the expected working-directory changes were actually applied before calling `dropDesktopStashEntry`. Only drop the stash entry when the pop is unambiguously confirmed successful; otherwise, propagate the error to the user.

### Proof of Concept
1. Trigger any local condition that causes `git stash pop <stash>` to exit with code `1` and empty `stderr` without actually restoring the stashed changes (e.g., a hook or lock contention that suppresses stderr output, or a future Git version altering this specific error text).
2. `popStashEntry` in `app/src/lib/git/stash.ts` catches the resulting `GitError`, sees `exitCode === 1` and empty `stderr`, and calls `dropDesktopStashEntry(repository, stashSha)`.
3. The stash reference is deleted permanently while the user's working directory does not contain the previously stashed changes — the changes are lost with no error shown to the user.

### Citations

**File:** app/src/lib/git/stash.ts (L160-196)
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
```

**File:** app/src/lib/git/stash.ts (L248-269)
```typescript
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
