[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4602-4621)
```typescript
    let strategy = explicitStrategy ?? this.uncommittedChangesStrategy

    // The user hasn't been presented with an explicit choice
    if (explicitStrategy === undefined) {
      // Even if the user has chosen to "always stash on current branch" in
      // preferences we still want to let them know changes might be lost
      if (strategy === UncommittedChangesStrategy.StashOnCurrentBranch) {
        if (hasChanges && stashEntry !== null) {
          const type = PopupType.ConfirmOverwriteStash
          this._showPopup({ type, repository, branchToCheckout: branch })
          return repository
        }
      }
    }

    // Always move changes to new branch if we're on a detached head, unborn
    // branch, or a protected branch.
    if (tip.kind !== TipState.Valid || currentBranchProtected) {
      strategy = UncommittedChangesStrategy.MoveToNewBranch
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

**File:** app/src/ui/notifications/pull-request-checks-failed.tsx (L392-406)
```typescript
  private onSubmit = async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const { dispatcher, repository, pullRequest } = this.props

    this.props.dispatcher.incrementMetric(
      'checksFailedDialogSwitchToPullRequestCount'
    )

    this.setState({ switchingToPullRequest: true })
    await dispatcher.selectRepository(repository)
    await dispatcher.checkoutPullRequest(repository, pullRequest)
    this.setState({ switchingToPullRequest: false })

    this.props.onDismissed()
  }
```
