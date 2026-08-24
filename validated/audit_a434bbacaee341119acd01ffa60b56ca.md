### Title
Automatic remote-URL rewrite driven by an unpinned owner/name lookup lets a renamed/reclaimed GitHub repository silently redirect what the user pushes and fetches - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
This bug class mirrors M-13's core defect: a security-relevant decision is driven by a cached/derived value (`meta.tuneBelowCapacity`) that is not kept consistent with the value it depends on (`meta.tuneIntervalCapacity`), so stale state silently corrupts subsequent behavior. In GitHub Desktop, the analogous stale-state chain is: `AppStore.matchGitHubRepository` re-derives `owner`/`name` from the *current* git remote text on every background refresh [1](#0-0) , `repositoryWithRefreshedGitHubRepository` uses that derived identity to fetch fresh repo metadata from the GitHub API [2](#0-1) , and `updateRemoteUrl` then compares the *previously cached* `gitHubRepository.cloneURL` (not the live API identity) against the current local remote to decide whether it's "safe" to silently rewrite the git remote URL to whatever `clone_url` the API just returned [3](#0-2) .

### Finding Description
`updateRemoteUrl` performs an unattended `git remote set-url` when three conditions hold: the protocol of the old and new URL match, the *cached* `gitHubRepository.cloneURL` still matches the user's current local remote, and the new API-supplied `clone_url` doesn't already match the current remote [4](#0-3) . This logic never re-validates that the underlying GitHub repository is still the *same* repository the user originally added as a remote — it only checks that the locally cached `cloneURL` field (last known state, itself populated from a previous successful API response) hasn't diverged from the local remote. `protocolsMatch` intentionally only compares URL schemes (`https:` vs `ssh:`), not hostnames [5](#0-4) , so nothing in this code path pins the update to the same host/owner beyond what the API itself returns for the `owner`/`name` pair that was derived from the *current* remote text.

Critically, that `owner`/`name` pair used for the API lookup is *not* a stable database identifier — it is recomputed from the raw remote URL string on every refresh via `matchGitHubRepository` [6](#0-5)  and `AppStore.matchGitHubRepository` [1](#0-0) . This background refresh runs periodically via the repository indicator updater and after every push/pull (`refreshBranchProtectionState`/`_refreshRepository` call sites) [7](#0-6) [8](#0-7) . If the real-world GitHub repository behind a given `owner/name` slug is deleted and that slug is reclaimed by a different party (a well-known "repo-jacking" pattern GitHub itself warns about for renamed/deleted repos and dangling forks), the next background refresh will fetch the new owner's `clone_url` for that slug and `updateRemoteUrl` will consider it a legitimate rename because `remoteUrlUnchanged` is computed only from the app's own stale cache, not from proof that the API result still describes the original repository. Desktop then silently rewrites the user's `origin` remote to the reclaimed repository via `gitStore.setRemoteURL` [9](#0-8) [10](#0-9) , with no dialog, confirmation, or diff shown to the user (contrast with `RepositorySettings`, where a manual remote URL edit at least goes through the dialog's own submit flow) [11](#0-10) .

### Impact Explanation
Once the remote URL is silently repointed, the user's next `git push` sends their code to the attacker-controlled repository (credential/IP exfiltration of proprietary work), and the next `git fetch`/`git pull` merges attacker-supplied commits into the user's local history/working tree without any explicit warning that the remote target changed. This is a "silent corruption of what the user commits or pushes" as defined in the valid-impact criteria — the destination of push/fetch is a git remote object that is entirely attacker-influenced (the GitHub repository content and metadata returned by the API), requiring no local access, no malware, and no unnatural user steps: the user just keeps using Desktop normally on a repository whose upstream slug was later reclaimed.

### Likelihood Explanation
The likelihood depends on an external, low-friction precondition that is common in practice: repositories/organizations being deleted or renamed and their name subsequently reclaimed. Desktop has no server-side protection against this because the matching logic in `repository-matching.ts`/`update-remote-url.ts` is deliberately based on the `owner/name` string rather than an immutable repository ID, and the automatic-rewrite feature is enabled unconditionally in the background refresh path with no opt-out or confirmation step visible in the reviewed code.

### Recommendation
- Pin repository identity to GitHub's immutable repository `id` (already available via the API) instead of re-deriving `owner`/`name` from the live remote text on every refresh, and only trust a rename when the API-returned `id` matches the previously stored `gitHubRepository` `id`.
- In `updateRemoteUrl`, additionally require that the new `clone_url`'s hostname matches the existing remote's hostname (not just protocol), and require a positive owner/name/id continuity check rather than only checking that the cache hasn't drifted.
- Surface a non-blocking but visible notification (or require confirmation) before silently rewriting a user's remote URL, rather than performing it transparently in a background refresh.

### Proof of Concept
1. User A clones `https://github.com/orgname/reponame` and lets Desktop track it as `origin`; Desktop stores `gitHubRepository.cloneURL = https://github.com/orgname/reponame` in `RepositoriesStore`.
2. `orgname` is later deleted/renamed on GitHub (e.g., org deleted or repo removed), and an attacker registers `orgname` and creates a repository named `reponame`.
3. On the next repository-indicator refresh (or right after any push/pull), `AppStore._refreshRepository` → `repositoryWithRefreshedGitHubRepository` → `matchGitHubRepository` re-derives `owner=orgname, name=reponame` from the still-unchanged local remote text, then calls `api.fetchRepository('orgname', 'reponame')`, which now resolves to the attacker's repository and returns the attacker's `clone_url` [12](#0-11) .
4. `updateRemoteUrl` finds `protocolsMatch = true` (both `https:`), `remoteUrlUnchanged = true` (cached `cloneURL` still equals the local remote, since the user never touched it), and `urlsMatch` comparisons pass, so it calls `gitStore.setRemoteURL('origin', <attacker clone_url>)` [9](#0-8) , silently repointing `origin` — confirmed by the existing unit test that this function performs the rewrite whenever the cached URL still matches the local remote and the new one differs [13](#0-12) .
5. The user's subsequent `git push`/`git fetch` operations now target the attacker's repository without any prompt.

Note: I was not able to find an explicit host-pinning or repository-ID check anywhere in the reviewed matching/update code, nor any confirmation dialog gating this specific automatic rewrite path; if such a guard exists elsewhere in the codebase outside indexed files, it was not discoverable through the available search tools.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4907)
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

**File:** app/src/lib/stores/app-store.ts (L5340-5344)
```typescript
          // manually refresh branch protections after the push, to ensure
          // any new branch will immediately report as protected
          await this.refreshBranchProtectionState(repository)

          await this._refreshRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L5602-5606)
```typescript
          // manually refresh branch protections after the push, to ensure
          // any new branch will immediately report as protected
          await this.refreshBranchProtectionState(repository)

          await this._refreshRepository(repository)
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

**File:** app/src/lib/repository-matching.ts (L28-46)
```typescript
/** Try to use the list of users and a remote URL to guess a GitHub repository. */
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
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

**File:** app/src/ui/repository-settings/repository-settings.tsx (L296-313)
```typescript
    if (this.state.remote && this.props.remote) {
      const trimmedUrl = this.state.remote.url.trim()

      if (trimmedUrl !== this.props.remote.url) {
        try {
          await this.props.dispatcher.setRemoteURL(
            this.props.repository,
            this.props.remote.name,
            trimmedUrl
          )
        } catch (e) {
          log.error(
            `RepositorySettings: unable to set remote URL at ${this.props.repository.path}`,
            e
          )
          errors.push(`Failed setting the remote URL: ${e}`)
        }
      }
```

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-81)
```typescript
  it("updates the repository's remote url when the github url changes", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)

    const originalUrl = gitStore.currentRemote.url
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }
    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert.notEqual(originalUrl, updatedUrl)
    assert.equal(gitStore.currentRemote.url, updatedUrl)
  })
```
