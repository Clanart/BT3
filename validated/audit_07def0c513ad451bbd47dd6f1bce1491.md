Based on my research, the closest structural analog to the "unrestricted `vestFor`" pattern — an unprivileged, unauthenticated entry point that lets an outside actor unilaterally mutate a victim's state with no consent check — is GitHub Desktop's custom URL protocol handler pipeline (`parse-app-url.ts` → `dispatchURLAction` → `openRepositoryFromUrl`/`openBranchNameFromUrl`/`openPullRequestFromUrl`). The OAuth branch of that pipeline is protected by a CSRF `state` token check [1](#0-0) , but the `open-repository-from-url` branch has no such origin/consent check.

### Title
Unauthenticated deep-link handler silently fetches and checks out attacker-controlled branch/PR refs in an existing repository - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
GitHub Desktop registers OS-level protocol handlers (`x-github-client://`, `github-mac://`, etc.) that any web page, email, chat message, or other application can invoke without any Desktop-side authentication or provenance check. `parseAppURL` accepts an `openRepositoryFromUrl` (aka `openRepo`) action carrying an attacker-chosen repository URL, `branch`, and `pr` number [2](#0-1) . When the URL is dispatched, `Dispatcher.openRepositoryFromUrl` resolves it: if the user already has a matching repository open, it silently fetches and checks out the attacker-supplied branch or pull request ref with no confirmation dialog and no verification that the person who clicked the link is the same as, or authorized by, the repository owner [3](#0-2) [4](#0-3) .

### Finding Description
The broken invariant: any action that changes a user's checked-out branch/HEAD should require the user to have actively chosen that action inside Desktop, not merely have clicked an external hyperlink. Here, `handleAppURL`/`dispatchURLAction` treats every protocol-launcher invocation as fully trusted [5](#0-4) , and `openBranchNameFromUrl`/`openPullRequestFromUrl` proceed straight to `_fetch` and `checkoutLocalBranch`/`_checkoutPullRequest` as soon as a matching repository is found — the only gate is `doesRepositoryMatchUrl` (a URL string comparison), not any consent or identity check [6](#0-5) . The lone safety net, `UncommittedChangesStrategy.AskForConfirmation`, only fires when the working directory is dirty [7](#0-6) ; on a clean tree the checkout proceeds immediately and silently, exactly mirroring the `vestFor` pattern of "no auth check, attacker can force state that persists / can't easily be undone."

### Impact Explanation
An attacker who gets a victim to click a crafted link (e.g. `x-github-client://openRepo/https://github.com/victim-org/private-repo?branch=pr%2F1&pr=1`) can force Desktop to fetch and check out an attacker-chosen ref inside a repository the victim already has open — without any explicit "yes, do this" prompt when the tree is clean. This can silently swap the developer's working tree to attacker-supplied code (e.g., before they run a build/test/commit), which is a "silent corruption of what the user commits" scenario matching the report's allowed-impact category, since subsequent commits/builds could operate on unintended code.

### Likelihood Explanation
High: no local access, no admin rights, and no prior malware are needed — only that the victim click a link, which OS protocol handlers deliver without a Desktop-side trust check. The `pr`/`branch` regex validation in `parseAppURL` only sanity-checks format, not intent or authorization [8](#0-7) .

### Recommendation
Require an explicit, in-app confirmation dialog before any deep-link-triggered checkout/fetch (not just when there are uncommitted changes), and/or restrict `open-repository-from-url` actions to same-origin/allow-listed hosts, similar to how the OAuth action is bound to a locally-generated CSRF `state` token before being honored.

### Proof of Concept
1. Victim has `victim-org/repo` already cloned/open in Desktop with a clean working tree.
2. Attacker sends victim a link: `x-github-client://openRepo/https://github.com/victim-org/repo?branch=pr%2F999&pr=999` (or any legitimate-looking branch name).
3. Victim clicks the link (e.g., in an email or webpage) — OS invokes Desktop's protocol handler automatically.
4. `handleAppURL` → `parseAppURL` → `dispatchURLAction('open-repository-from-url')` → `openPullRequestFromUrl`/`openBranchNameFromUrl` executes `_fetch` and checks out the attacker-specified ref with no additional prompt, since the code path in `dispatcher.ts` lines 1940-2048 has no confirmation step for a clean tree.

Note: I was not able to fully trace what UI-level "trust this app to open?" OS dialogs might interpose on each platform (Windows/macOS differ), which could partially mitigate first-time invocation; this is an OS-level, not Desktop-code-level, mitigation and is out of scope for this local-code analysis.

### Citations

**File:** app/src/lib/stores/sign-in-store.ts (L332-346)
```typescript
  public async resolveOAuthRequest(action: IOAuthAction) {
    if (!this.state || this.state.kind !== SignInStep.Authentication) {
      return
    }

    if (!this.state.oauthState) {
      return
    }

    if (this.state.oauthState.state !== action.state) {
      log.warn(
        'requestAuthenticatedUser was not called with valid OAuth state. This is likely due to a browser reloading the callback URL. Contact GitHub Support if you believe this is an error'
      )
      return
    }
```

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1996)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
  }

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2048)
```typescript
  private async openPullRequestFromUrl(
    url: string,
    pr: string
  ): Promise<RepositoryWithGitHubRepository | null> {
    const pullRequest = await this.appStore.fetchPullRequest(url, pr)

    if (pullRequest === null) {
      return null
    }

    // Find the repository where the PR is created in Desktop.
    let repository: Repository | null =
      this.getRepositoryFromPullRequest(pullRequest)

    if (repository !== null) {
      await this.selectRepository(repository)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      log.warn(
        `Open Repository from URL failed, did not find or clone repository: ${url}`
      )
      return null
    }
    if (!isRepositoryWithGitHubRepository(repository)) {
      log.warn(
        `Received a non-GitHub repository when opening repository from URL: ${url}`
      )
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    if (pullRequest.head.repo === null) {
      return null
    }

    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )

    return repository
  }
```

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
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
