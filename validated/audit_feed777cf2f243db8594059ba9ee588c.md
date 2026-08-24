## Title
Heuristic exit-code handling in stash creation/pop can misclassify a failed operation as success, silently discarding uncommitted work — ([File: app/src/lib/git/stash.ts])

### Summary
This is the closest real analog in this codebase to the reported "unsafe approve" pattern: code that infers an external operation *succeeded* from an incomplete/heuristic signal instead of verifying the actual outcome, and then proceeds to mutate state (here, working-directory contents and stash entries) based on that unverified assumption. In `Basket.sol` the flawed assumption was "the `approve` call succeeded." In GitHub Desktop, the analogous flawed assumption is "`git stash push`/`git stash pop` succeeded because `exitCode === 1` and stderr didn't start with `error:`" — an assumption the code's own comment acknowledges is unsound.

### Finding Description
`createDesktopStashEntry` runs `git stash push` and treats a non-zero (`1`) exit code as success whenever stderr contains no line beginning with `error: `: [1](#0-0) 

The comment directly above this logic documents that the heuristic is known to be wrong in at least one case (an unborn repository returns exit code 1 with no stash created, yet the check would treat it as success): [2](#0-1) 

`popStashEntry` has the same class of heuristic: it treats exit code `1` with an *empty* stderr as "the pop actually succeeded," and reacts by permanently dropping the stash entry (`dropDesktopStashEntry`) — i.e., deleting the user's backup of their own changes based on an assumption, not a verified successful `apply`: [3](#0-2) 

Both of these unverified "success" results flow directly into the checkout logic used when Desktop moves a user's uncommitted changes across a branch switch. `checkoutAndBringChanges` stashes the working directory, checks out the target branch, and then unconditionally pops the stash, trusting that the stash actually contains the user's changes: [4](#0-3) 

If `createDesktopStashEntry` returns `true` for a case where no stash was actually created (as the comment describes for the unborn-branch case, and as could plausibly occur for other non-`error:`-prefixed failures such as a failing clean/smudge filter or hook-driven `.gitattributes` behavior triggered by repository content), Desktop proceeds to `checkoutIgnoringChanges` — which does a hard checkout — believing the user's edits are safely stashed. They are not. The failure is only logged via `log.info` (not surfaced to the user), so the working directory is silently overwritten/discarded with no error shown.

### Impact Explanation
This does not require attacker code execution, but it fits the "silent corruption of what the user commits or pushes" category: uncommitted local changes can be silently and irrecoverably discarded during a routine branch switch because the app trusted an unverified heuristic about whether an external `git` process succeeded, exactly mirroring the unsafe-`approve` pattern of assuming success from an ambiguous return signal rather than validating it. Existing guards (`GitError`/`expectedErrors`/`successExitCodes` in `app/src/lib/git/core.ts`) are explicitly bypassed here by a hand-rolled `.catch()` heuristic layered on top of them, so the core success/error handling in `core.ts` does not protect this path.

### Likelihood Explanation
Low-to-moderate. The known trigger (unborn branch + local changes) is a normal, non-adversarial repository state, so this can occur without any attacker involvement at all. It is plausible, though not proven here, that repository-controlled behavior (e.g., filters/hooks affecting `git stash`'s stderr output) could increase the odds of hitting the same misclassification, but the primary and demonstrated trigger is a mundane repository state rather than a required attacker action.

### Recommendation
Do not infer success from the *absence* of an `error:`-prefixed stderr line or from an empty stderr string. Instead, verify the actual state directly: after `git stash push`, check `git stash list`/`git rev-parse` for the newly created stash ref; after `git stash pop`, verify the stash was actually removed by comparing `git stash list` before/after (or checking the stash ref no longer resolves) before calling `dropDesktopStashEntry`. If verification is inconclusive, surface the error to the user rather than silently proceeding with a destructive checkout.

### Proof of Concept
1. Create a fresh (unborn) repository with no commits.
2. Create a file and stage/modify it so the working directory has changes.
3. Trigger a branch checkout flow in Desktop that uses `UncommittedChangesStrategy.MoveToNewBranch`/`StashOnCurrentBranch`, which calls `createStashEntry` → `createDesktopStashEntry`.
4. `git stash push` exits with code `1` and message `"You do not have the initial commit yet"` (no `error:` prefix), matching the documented case in the code comment: [5](#0-4) 
5. `createDesktopStashEntry` returns `true` even though no stash was created; `checkoutAndBringChanges` proceeds with `checkoutIgnoringChanges`, discarding the user's working-directory changes with no stash to restore them from and no error shown to the user.

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

**File:** app/src/lib/git/stash.ts (L238-271)
```typescript
export async function popStashEntry(
  repository: Repository,
  stashSha: string
): Promise<void> {
  // ignoring these git errors for now, this will change when we start
  // implementing the stash conflict flow
  const expectedErrors = new Set<DugiteError>([DugiteError.MergeConflicts])
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
  }
}
```

**File:** app/src/lib/stores/app-store.ts (L4705-4734)
```typescript
  private async checkoutAndBringChanges(
    repository: Repository,
    branch: Branch,
    currentRemote: IRemote | null
  ) {
    try {
      await this.checkoutIgnoringChanges(repository, branch, currentRemote)
    } catch (checkoutError) {
      if (!isLocalChangesOverwrittenError(checkoutError)) {
        throw checkoutError
      }

      const stash = (await this.createStashEntry(repository, branch))
        ? await getLastDesktopStashEntryForBranch(repository, branch)
        : null

      // Failing to stash the changes when we know that there are changes
      // preventing a checkout is very likely due to assume-unchanged or
      // skip-worktree. So instead of showing a "could not create stash" error
      // we'll show the checkout error to the user and let them figure it out.
      if (stash === null) {
        throw checkoutError
      }

      await this.checkoutIgnoringChanges(repository, branch, currentRemote)
      await popStashEntry(repository, stash.stashSha)

      this.statsStore.increment('changesTakenToNewBranchCount')
    }
  }
```
