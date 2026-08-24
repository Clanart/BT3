## Analysis

The Malt report's broken invariant is: *"a function assumes a specific operation outcome without verifying it, then irreversibly disposes of the user's value based on that unverified assumption."* The closest and best-supported analog in GitHub Desktop is the stash-pop error-handling heuristic in `popStashEntry`. [1](#0-0) 

### Title
Unverified exit-code heuristic in `popStashEntry` permanently deletes a user's stash without confirming it was applied - (File: `app/src/lib/git/stash.ts`)

### Summary
`popStashEntry` treats a `git stash pop` failure (`exitCode === 1` with empty `stderr`) as proof that the stash *was* successfully applied to the working directory, and on that assumption calls `dropDesktopStashEntry` to delete it. There is no verification that the stash's contents actually reached the working directory before deletion — mirroring the Bonding.sol bug where an edge-case return (`amountMalt == 0`) was not checked before the caller proceeded as if funds had already been returned to the user.

### Finding Description [2](#0-1) 

```
await git(args, repository.path, 'popStashEntry', {
  expectedErrors,
}).catch(e => {
  if (
    e instanceof GitError &&
    e.result.exitCode === 1 &&
    e.result.stderr.length === 0
  ) {
    log.info(...)
    // bye bye
    return dropDesktopStashEntry(repository, stashSha)
  }
  return Promise.reject(e)
})
```

The code comment itself acknowledges the fragility of this heuristic: *"popping a stashes that create conflicts in the working directory report an exit code of `1` and are not dropped after being applied... Not the greatest approach but stash isn't very communicative."* The sibling function `createDesktopStashEntry` documents, with a concrete counter-example, that the "exit 1 + no `error:` line ⇒ operation actually succeeded" assumption does **not** universally hold for `git stash` subcommands: [3](#0-2) 

That comment explicitly shows a case (`git stash push` in an unborn repo) where exit code 1 occurs with **no** stash created and no `error:`-prefixed stderr line — the exact same signature `popStashEntry` uses to conclude "stash was popped." Because git's messaging for `stash` operations is not the reliable substitute for actually checking working-directory state, any `git stash pop` failure mode that produces exit code 1 with empty stderr — e.g. path/case-collisions on case-insensitive filesystems, permission errors during checkout-index, or other apply-time failures triggered by adversarial tree content in a fetched/checked-out branch that is then stashed and popped — will cause Desktop to conclude success and irrevocably call `dropDesktopStashEntry`, which deletes the only backup of the user's uncommitted changes: [4](#0-3) 

### Impact Explanation
If the heuristic is wrong, the user's uncommitted work — already removed from the working directory by the earlier stash creation during a branch switch (`checkoutAndBringChanges`/`checkoutAndLeaveChanges`) — is deleted from the reflog-backed stash with no recovery path, and `_popStashEntry` reports success to the UI: [5](#0-4) [6](#0-5) 

This is a silent, permanent loss of the user's changes analogous to LP tokens becoming stuck in `Bonding.sol` — the value (here, uncommitted code) is destroyed because the caller trusted an ambiguous signal instead of confirming the actual outcome.

### Likelihood Explanation
This path is reached automatically any time Desktop moves changes across a branch switch (`UncommittedChangesStrategy.MoveToNewBranch`), which is a routine, low-friction user action, not an unusual admin/local-access scenario. The trigger condition — `git stash pop` returning exit code 1 with empty stderr while not fully applying the stash — depends on git/platform-specific edge cases (e.g., case-folding collisions from a malicious tree, filesystem write races, or apply-time errors); this is a real, previously observed git-messaging inconsistency (as documented in-repo for the sibling `createDesktopStashEntry` heuristic), making it plausible rather than purely theoretical, but it is not deterministically reproducible from arbitrary attacker input, so likelihood is moderate.

### Recommendation
Do not infer success from exit code/stderr alone. After a `git stash pop` failure, explicitly verify whether the stash's tree was actually merged into the working directory (e.g., diff the working directory/index against the stash's tree object, or check `git stash list` plus working directory status) before calling `dropDesktopStashEntry`. If verification is inconclusive, surface the error to the user instead of silently dropping the stash, consistent with the judge's recommendation in the referenced Malt finding that ambiguous edge cases should fail safe (revert/preserve) rather than assume success.

### Proof of Concept
1. Trigger a workflow that stashes changes and pops them via `checkoutAndBringChanges` (switch branches with `MoveToNewBranch` strategy) — see [5](#0-4) .
2. Arrange for the subsequent `git stash pop` to fail with exit code 1 and empty stderr without actually restoring the stash content to the working directory (e.g. via a filesystem case-collision or an apply-time failure introduced by content in the checked-out tree, matching the class of "exit 1, no error output, no actual effect" documented in-repo at lines 163-197 of `stash.ts`).
3. Observe that `popStashEntry`'s `.catch` branch matches (`exitCode === 1 && stderr.length === 0`) and calls `dropDesktopStashEntry`, permanently removing the stash entry.
4. Confirm the working directory does not contain the previously stashed changes, yet `_popStashEntry` completed without throwing and the app reports success — the user's changes are unrecoverable.

### Citations

**File:** app/src/lib/git/stash.ts (L163-197)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L4705-4733)
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
```

**File:** app/src/lib/stores/app-store.ts (L8893-8902)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public async _popStashEntry(repository: Repository, stashEntry: IStashEntry) {
    await popStashEntry(repository, stashEntry.stashSha)
    log.info(
      `[AppStore. _popStashEntry] popped stash with commit id ${stashEntry.stashSha}`
    )

    this.statsStore.increment('stashRestoreCount')
    await this._refreshRepository(repository)
  }
```
