## Title
Attacker-controlled deep link silently fetches and switches branches in an already-cloned repository without user confirmation - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
GitHub Desktop registers a custom protocol handler (`x-github-client://`, `github-mac://`, etc.) that is parsed by `parseAppURL` and dispatched to `Dispatcher.dispatchURLAction`. When the action is `open-repository-from-url` with a `branch` parameter, and the target repository is **already cloned** by the user, Desktop does not show any clone/open confirmation dialog (that dialog is only shown for new clones). Instead it silently fetches from the remote and checks out the attacker-specified branch on the user's existing, trusted local repository, with no popup asking the user to approve this action — analogous to the `claimAndCompound` bug where an action was performed "on behalf of" a user without checking that the user opted in.

### Finding Description
The URL action is parsed here: [1](#0-0) 

It is routed unconditionally to `openRepositoryFromUrl`: [2](#0-1) 

When a `branch` is present, `openBranchNameFromUrl` is invoked. For a repository the user already has open in Desktop (`doesRepositoryMatchUrl` matches by remote/upstream URL), `openOrCloneRepository` takes the "existing repository" branch and just calls `selectRepository` — no popup, no user prompt: [3](#0-2) 

Immediately after, without any additional confirmation, Desktop refreshes state, force-fetches from the remote, and checks out the requested branch: [4](#0-3) 

`checkoutLocalBranch` resolves the branch against `state.branchesState.allBranches` (which includes remote-tracking branches populated by the fetch that just ran) and, if found and different from the current tip, silently calls `checkoutBranch`: [5](#0-4) 

The underlying `_checkoutBranch` in the store only shows a confirmation dialog (`StashAndSwitchBranch`/`ConfirmOverwriteStash`) when there are **uncommitted local changes**; if the working directory is clean, the branch switch proceeds with zero user interaction: [6](#0-5) 

So the missing check is: there is no verification that the user has "opted in" (i.e., explicitly clicked to open/confirm this specific action) before an already-open, trusted repository is mutated — fetched from a remote and switched to a different branch — purely because the user clicked a link containing a URL that happens to match one of their existing repos.

### Impact Explanation
An attacker who knows (or guesses) the public GitHub URL of a repository the victim has already cloned in Desktop (this is trivially knowable for any public repo, and the URL is exactly the `clone_url`/`htmlURL` shown on GitHub) can craft a link such as `x-github-client://openRepo/https://github.com/victim-org/victim-repo?branch=<attacker-branch>`. If the victim clicks it (e.g., embedded in a webpage, chat message, or "Open in Desktop" style button), Desktop will:
1. Silently fetch from the remote.
2. Silently check out `attacker-branch` in the victim's existing local working copy — no dialog, no confirmation — as long as there are no pending uncommitted changes.

This corrupts the state of what the user believes they are working on/will commit: a subsequent commit, build, or push by the victim could unknowingly operate on attacker-controlled branch content. If the remote itself is attacker-influenced (e.g., a compromised fork/organization member, or a malicious push to a branch the attacker has write access to), this becomes a vector for silently steering a developer's working tree toward malicious code without any explicit consent step, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to click a crafted deep link and to already have the targeted repository cloned locally with the protocol handler registered (default install behavior), and it only fully bypasses confirmation when the working directory is clean (a common state for developers switching context). No admin rights, malware, or leaked credentials are required — only a link click, which is within the accepted attacker model for this analog task.

### Recommendation
Before silently fetching and checking out a branch triggered by a URL-based action (`open-repository-from-url` with `branch`), require an explicit user confirmation step analogous to the one already used for the "new clone" path (`PopupType.CloneRepository`) — e.g., show a popup naming the target repository and branch and requiring the user to approve the fetch/checkout, rather than performing it unconditionally in `openBranchNameFromUrl`.

### Proof of Concept
1. Victim has already cloned `https://github.com/victim-org/victim-repo` in GitHub Desktop and has no pending uncommitted changes.
2. Attacker pushes/creates a branch `evil-branch` on that remote (or a fork the victim's remote configuration resolves to) containing modified source/build files.
3. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/victim-org/victim-repo?branch=evil-branch`.
4. Victim clicks the link. Desktop's `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl` executes: `_refreshRepository`, `_fetch`, then `checkoutLocalBranch` — all without any confirmation dialog — leaving the victim's working directory checked out to `evil-branch`.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1975-1996)
```typescript
  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2188-2212)
```typescript
  private async checkoutLocalBranch(repository: Repository, branch: string) {
    let shouldCheckoutBranch = true

    const state = this.repositoryStateManager.get(repository)
    const branches = state.branchesState.allBranches

    const { tip } = state.branchesState

    if (tip.kind === TipState.Valid) {
      shouldCheckoutBranch = tip.branch.nameWithoutRemote !== branch
    }

    const localBranch = branches.find(b => b.nameWithoutRemote === branch)

    // N.B: This looks weird, and it is. _checkoutBranch used
    // to behave this way (silently ignoring checkout) when given
    // a branch name string that does not correspond to a local branch
    // in the git store. When rewriting _checkoutBranch
    // to remove the support for string branch names the behavior
    // was moved up to this method to not alter the current behavior.
    //
    // https://youtu.be/IjmtVKOAHPM
    if (shouldCheckoutBranch && localBranch !== undefined) {
      await this.checkoutBranch(repository, localBranch)
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L4602-4629)
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

    if (strategy === UncommittedChangesStrategy.AskForConfirmation) {
      if (hasChanges) {
        const type = PopupType.StashAndSwitchBranch
        this._showPopup({ type, branchToCheckout: branch, repository })
        return repository
      }
    }
```
