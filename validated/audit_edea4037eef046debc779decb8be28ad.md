### Title
`createDesktopStashEntry` misclassifies a failed `git stash push` as success on an unborn repository, causing Desktop to proceed as if changes were safely saved - (File: `app/src/lib/git/stash.ts`)

### Summary
`createDesktopStashEntry` treats a failed `git stash push` (exit code 1, no `error:`-prefixed stderr line) as a successful stash creation. This is the same class of bug as the Foundation report: the caller trusts an external operation's ambiguous non-throwing/non-reverting outcome as "success" without verifying that the expected side effect (a stash object / a minted token) actually occurred, and then proceeds to perform a destructive follow-on action based on that false assumption.

### Finding Description
`createDesktopStashEntry` runs `git stash push -m <message>` and wraps failures in a `.catch()` handler: [1](#0-0) 

The handler's own comment documents the ambiguity: if `git stash push` exits with code `1` and stderr contains no line beginning with `error:`, the code assumes "a valid stash was created" and returns the (failed) result as if it succeeded — ultimately causing `createDesktopStashEntry` to return `true` (stash created). But the comment explicitly calls out a counter-example where this heuristic is wrong: running `git stash push` in an **unborn repository** (no initial commit) exits `1` with the message `You do not have the initial commit yet` and **no stash is created**, yet this message does not start with `error:`, so the heuristic misclassifies it as success: [2](#0-1) 

Just like `mintCountTo()` returning `0` instead of reverting on the last mint (an in-spec-but-ambiguous non-failure signal), `git stash`'s exit code/stderr format does not reliably distinguish "nothing to stash because of an unrelated fatal condition" from "stash succeeded despite a warning." The caller (`createDesktopStashEntry`) treats the absence of an `error:` prefix as proof of success rather than verifying the actual invariant — that a stash object now exists — the same missing-guard pattern flagged in the original report (`balanceOf` check was recommended there; here, a `getStashes`/ref check on `refs/stash` would be the analog).

The sibling function `popStashEntry` has the identical pattern in reverse, assuming a stash was applied and proceeding to drop it based only on `exitCode === 1 && stderr.length === 0`: [3](#0-2) 

### Impact Explanation
If `createDesktopStashEntry` is invoked against a repository state where `git stash push` fails with exit code 1 and no `error:`-prefixed stderr (e.g., an unborn/no-initial-commit repository, or other stash-refusal messages that don't use that exact prefix), the function returns `true` telling the caller "your working directory changes are safely stashed." Any subsequent destructive git operation performed by Desktop under that assumption (e.g., a branch checkout/reset that is normally safe because changes were supposedly stashed first) proceeds without the actual safety net existing, risking silent loss or corruption of the user's uncommitted work — directly analogous to the "user loses funds without receiving the NFT" outcome in the source report, translated to "user loses local changes without an actual stash to recover them from."

### Likelihood Explanation
The trigger condition (an unborn repository with no initial commit) is an ordinary, unprivileged git repository state — a repository can be cloned/fetched in this state, and the failure text `You do not have the initial commit yet` is git's real behavior for `stash push` in that state, as literally documented in the code's own comment. This makes the misclassification straightforward to hit without any special git version, config, or malicious tooling — only a specific, legitimately reachable repository state. I could not fully trace every call site in `app-store.ts` within the available context to confirm which specific checkout/branch-switch flow consumes the `true` return value and what destructive operation follows it, so the exact downstream data-loss action (e.g., `checkoutBranch` behavior after a "successful" stash) is not verified end-to-end from the indexed code alone.

### Recommendation
Do not infer success from the absence of an `error:` prefix. After a `git stash push` that exits with a non-zero code, verify the actual invariant directly (e.g., call `getStashes`/inspect `refs/stash` to confirm a new stash entry with the expected message exists) before reporting success to the caller, mirroring the `balanceOf` verification recommended in the original report. Apply the same verification to `popStashEntry`'s drop-on-assumed-success path.

### Proof of Concept
1. Clone/create a repository with no commits (unborn HEAD) — a state reachable by cloning a bare/empty remote.
2. Create some working-directory changes (untracked or staged files can exist even without HEAD).
3. Trigger a Desktop flow that calls `createDesktopStashEntry` (e.g., a branch switch path that stashes changes before checkout).
4. `git stash push -m <message>` internally fails with exit code 1 and stderr `You do not have the initial commit yet` (no `error:` prefix).
5. Per `app/src/lib/git/stash.ts:178-196`, the catch handler treats this as a successful stash and `createDesktopStashEntry` returns `true`.
6. Desktop's caller proceeds with the follow-on operation believing the working directory changes are safely stashed, even though `git stash list`/`refs/stash` shows no such stash exists.

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

**File:** app/src/lib/git/stash.ts (L251-269)
```typescript
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
