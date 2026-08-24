## Title
Silent, unconfirmed remote-URL rewrite from GitHub API data lets a malicious repo owner redirect the user's next push — (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` automatically rewrites a user's local `origin` remote URL based on data returned by the GitHub API (`clone_url`), with no user confirmation, whenever the app performs a background repository refresh. Because this happens silently and asynchronously relative to the user's decision to push, a party who controls the GitHub API response for the repository (the repo owner, or an org member with rename/transfer rights) can flip the remote's clone URL immediately before a scheduled/queued push executes, causing the user's commits to be sent to a URL the user never approved — a direct structural analog to the reported "owner front-runs a value right before the pending action executes" bug class.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository`, which runs during routine, non-interactive repository refresh cycles (e.g. account-change refresh, periodic refresh, and as a precursor step before publishing/pushing): [1](#0-0) 

The refresh flow:
1. Determines `owner`/`name` from the **currently configured local remote URL** via `matchGitHubRepository`. [2](#0-1) 
2. Calls the GitHub API fresh: `api.fetchRepository(owner, name)` — this value is entirely controlled by GitHub-side state (owner/attacker actions such as a repository rename/transfer). [3](#0-2) 
3. Passes the API response straight into `updateRemoteUrl`, which — if the protocol matches and the current remote still equals the previously cached `cloneURL` — calls `gitStore.setRemoteURL(...)` to rewrite `origin` **without prompting the user**: [4](#0-3) 

`setRemoteURL` executes `git remote set-url` directly against the working copy and reloads remotes into app state: [5](#0-4) [6](#0-5) 

The very next push reads whichever remote is currently cached in state — populated by this same silent update — and uses it to build the push target and credential/proxy environment: [7](#0-6) [8](#0-7) 

No existing guard requires user acknowledgement of a remote-URL change originating from the API; the only checks are "protocol unchanged" and "remote wasn't manually edited since we last cached the API's clone_url" — both of which are satisfied in the normal, unmodified case, so the rewrite happens transparently. This is functionally the same invariant break as the report: a value the user relies on when deciding to act (here: "which remote will `git push` target") can be swapped out by a party who doesn't need the user's consent, right before the pending action (the push) executes.

### Impact Explanation
If exploited, a user's local commits — potentially containing proprietary code — can be pushed to a repository URL chosen by the attacker instead of the one the user believes they're pushing to, with no dialog or confirmation shown. This matches the "silent corruption of what the user commits or pushes" impact category explicitly called out as valid. Because `envForRemoteOperation`/credential resolution is also keyed off `remote.url`, a divergent case (e.g. GHE server compromise or MITM against a GHE endpoint the user already trusts) could additionally influence which stored credentials get sent to which host.

### Likelihood Explanation
This does not require local/physical access, admin rights, or pre-existing malware — the only requirement is control over what the GitHub API/GHE server returns for `repos/{owner}/{name}` (trivially true for a normal repo owner via legitimate rename/transfer actions, or for anyone able to influence a self-hosted GHE instance's API responses). The refresh path (`repositoryWithRefreshedGitHubRepository`) runs automatically as part of normal Desktop usage, not requiring unnatural user steps, and updates happen silently in the background, making the timing/front-run practical to align with a queued or imminent push.

### Recommendation
- Never silently rewrite a remote URL from background/API-driven refresh. At minimum, surface a confirmation dialog (similar to the existing `RepositorySettings` remote-URL change flow) before calling `setRemoteURL` from `updateRemoteUrl`.
- Tie the update to a stable repository identity (API repo ID) rather than solely owner/name + clone_url string comparison, so a rename/transfer to a *different* underlying repository can be distinguished from an in-place URL change.
- Consider deferring/blocking any pending push if the remote URL changes between the time the push was initiated and the time it's executed, and re-confirm the destination with the user.

### Proof of Concept
1. User A has GitHub Desktop configured with `origin` = `https://github.com/attacker-org/legit-repo.git`, cached `gitHubRepository.cloneURL` matches.
2. Attacker (owner/admin of `attacker-org/legit-repo`) renames/transfers the repository such that `repos/attacker-org/legit-repo` now resolves to a different underlying repo record whose `clone_url` differs (e.g. attacker transfers ownership and simultaneously recreates a same-named repo elsewhere, or simply changes the repo's canonical clone URL via a rename), timed to occur while User A has uncommitted work queued to push.
3. Desktop's background refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`) fetches the new `clone_url` and, finding protocol unchanged and the local remote still matching the old cached `cloneURL`, silently calls `git remote set-url origin <new-url>` with no prompt (`app/src/lib/stores/updates/update-remote-url.ts:42-44`).
4. User A's next `Push` (`performPush` in `app-store.ts`) reads the now-modified `remote` from state and pushes their commits to the attacker-selected URL instead of the one User A saw in the UI moments earlier.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4914)
```typescript
  private async repositoryWithRefreshedGitHubRepository(
    repository: Repository
  ): Promise<Repository> {
    const repoStore = this.repositoriesStore
    const match = await this.matchGitHubRepository(repository)

    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
    }

    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)

    await this.refreshBranchProtectionState(freshRepo)
    return freshRepo
  }
```

**File:** app/src/lib/stores/app-store.ts (L4964-4977)
```typescript
  private async matchGitHubRepository(
    repository: Repository
  ): Promise<IMatchedGitHubRepository | null> {
    const gitStore = this.gitStoreCache.get(repository)

    if (!gitStore.defaultRemote) {
      await gitStore.loadRemotes()
    }

    const remote = gitStore.defaultRemote
    return remote !== null
      ? matchGitHubRepository(this.accounts, remote.url)
      : null
  }
```

**File:** app/src/lib/stores/app-store.ts (L5191-5230)
```typescript
  private async performPush(
    repository: Repository,
    options?: PushOptions
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { remote } = state
    if (remote === null) {
      this._showPopup({
        type: PopupType.PublishRepository,
        repository,
      })

      return
    }

    return this.withPushPullFetch(repository, async () => {
      const branch = this.getBranchToPush(repository, options)

      if (branch === undefined) {
        return
      }

      const remoteName = branch.upstreamRemoteName || remote.name

      const pushTitle = `Pushing to ${remoteName}`

      // Emit an initial progress even before our push begins
      // since we're doing some work to get remotes up front.
      this.updatePushPullFetchProgress(repository, {
        kind: 'push',
        title: pushTitle,
        value: 0,
        remote: remoteName,
        branch: branch.name,
      })

      // Let's say that a push takes roughly twice as long as a fetch,
      // this is of course highly inaccurate.
      let pushWeight = 2.5
      let fetchWeight = 1
```

**File:** app/src/lib/api.ts (L972-988)
```typescript
  /** Fetch a repo by its owner and name. */
  public async fetchRepository(
    owner: string,
    name: string
  ): Promise<IAPIFullRepository | null> {
    try {
      const response = await this.ghRequest('GET', `repos/${owner}/${name}`)
      if (response.status === HttpStatusCode.NotFound) {
        log.warn(`fetchRepository: '${owner}/${name}' returned a 404`)
        return null
      }
      return await parsedResponse<IAPIFullRepository>(response)
    } catch (e) {
      log.warn(`fetchRepository: an error occurred for '${owner}/${name}'`, e)
      return null
    }
  }
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-45)
```typescript
export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  // I'm not sure when these early exit conditions would be met. But when they are
  // we don't have enough information to continue so exit early!
  if (gitStore.defaultRemote === null) {
    return
  }

  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)

  // Verify that protocol hasn't changed. If it has we don't want
  // to alter the protocol in case they are relying on a specific one.
  // If protocol is null that implies the url is a ssh url
  // of the format git@github.com:octocat/Hello-World.git, which
  // can't be parsed by URL.parse. In this case we assume the user
  // manually configured their remote to use this format and we don't
  // want to change what they've done just to be safe
  const parsedRemoteUrl = URL.parse(remoteUrl)
  const parsedUpdatedRemoteUrl = URL.parse(updatedRemoteUrl)
  const protocolsMatch =
    parsedRemoteUrl.protocol !== null &&
    parsedUpdatedRemoteUrl.protocol !== null &&
    parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol

  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
}
```

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
  }
```

**File:** app/src/lib/git/remote.ts (L56-64)
```typescript
/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
}
```

**File:** app/src/lib/git/push.ts (L48-61)
```typescript
export async function push(
  repository: Repository,
  remote: IRemote,
  localBranch: string,
  remoteBranch: string | null,
  tagsToPush: ReadonlyArray<string> | null,
  options?: PushOptions,
  progressCallback?: (progress: IPushProgress) => void
): Promise<void> {
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```
