## Analysis

The Folio-Owner report describes a trusted-but-unaccountable actor silently corrupting shared state (the basket) without any code change, violating the app's threat model. The closest verified analog in `Kirstentat/desktop--012` is **the `updateRemoteUrl` background sync silently rewriting a repository's git remote based on a GitHub API response, with no re-validation that the new owner/name/host is actually the same repository the user trusts** — allowing the value the user pushes/fetches against to be corrupted using nothing but attacker-controlled API data, which the task explicitly allows as an attacker primitive ("attacker controls...a GitHub API object").

### Title
Silent, unauthenticated remote-URL rewrite from GitHub API `clone_url` allows redirecting pushes/fetches to an attacker-controlled repository - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`repositoryWithRefreshedGitHubRepository` in `app-store.ts` calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` every time Desktop refreshes GitHub repository metadata (on selecting a repo, on fetch, on account change, on background indicator refresh) [1](#0-0) [2](#0-1) . `updateRemoteUrl` will overwrite the local `origin` remote URL with whatever `clone_url` the API returned, as long as (a) the URL scheme matches and (b) the *previously cached* `GitHubRepository.cloneURL` still matches the current local remote [3](#0-2) .

### Finding Description
The gating logic only checks:
1. `protocolsMatch` — both old and new URL use the same scheme (`https:` vs `https:`, or both non-parseable "ssh-like"),
2. `remoteUrlUnchanged` — the *current* local remote still equals the last-known `gitHubRepository.cloneURL` (i.e., the user hasn't manually edited the remote), and
3. `!urlsMatch` — the new `clone_url` differs from the current remote.

If all three hold, it calls `gitStore.setRemoteURL(name, updatedRemoteUrl)` and overwrites the remote unconditionally [4](#0-3) . Critically, there is **no check that the new `clone_url`'s owner/name/hostname is a legitimate rename of the same underlying repository** (e.g., same repo ID) — it only checks that the URL is *different* from before. The `urlMatchesRemote`/`urlsMatch` helpers used elsewhere in the codebase to compare hostname+owner+name [5](#0-4)  are used here purely to detect a *change*, not to constrain what that change is allowed to be. The unit tests confirm this: an update to a totally different `owner/name` string is accepted and applied as long as protocol matches and the local remote wasn't hand-edited [6](#0-5) .

The `apiRepo` value is not attacker-verified server-side content the user reviews — it comes directly from `api.fetchRepository(owner, name)` against whichever account/endpoint is associated with the repo, including self-hosted GitHub Enterprise endpoints the user has added [7](#0-6) . A GHE server (or a MITM'd/compromised GitHub Enterprise deployment, which is an explicitly in-scope "GitHub API object" attacker per the task) that returns a crafted `clone_url` pointing to a completely different host/owner/name for the same `owner/repos/name` API path can cause Desktop to silently retarget the user's `origin` remote — with no dialog, confirmation, or diff shown to the user.

### Impact Explanation
Once the remote is silently rewritten, all subsequent `git push`/`git fetch` operations the user performs through Desktop's UI transparently go to the attacker-designated remote instead of the repository the user believes they're working with. This is "silent corruption of what the user commits or pushes" — the exact category the task calls out as valid impact: a user's next push (potentially containing proprietary/private source code) is exfiltrated to attacker infrastructure without any user action beyond normal Desktop usage (opening the repo, fetching, or letting the background repository-indicator refresh run) [8](#0-7) . Because the rewrite happens transparently on background/indicator refreshes that run without user interaction [9](#0-8) , the user has no visual cue that their remote has changed before their next push.

### Likelihood Explanation
The path is reachable any time Desktop talks to a non-github.com endpoint under attacker influence (malicious/compromised GHE instance, or any account whose backing API server is not github.com and can be spoofed/MITM'd) — this matches the task's allowed vector of an "attacker-controlled GitHub API object." The precondition ("user hasn't manually edited the remote away from the last known `cloneURL`") is the common case for the vast majority of users who never touch their remotes by hand, so the attack is not gated by unusual user behavior.

### Recommendation
- **Short term:** Do not trust a bare `clone_url` change as sufficient grounds for an unattended remote rewrite. Require that the underlying repository identity (a stable GitHub repository `id`, not owner/name/url) match between the previously stored `GitHubRepository` and the newly fetched `apiRepo` before calling `setRemoteURL`, and surface a confirmation/notification to the user when the remote is about to change.
- **Long term:** Treat GitHub API responses from any non-default/enterprise endpoint as partially untrusted input with respect to mutating local git configuration, and document this threat model explicitly (mirroring the recommendation in the original report) so that automatic-remote-rewrite logic is reviewed against it.

### Proof of Concept
1. User adds a GitHub Enterprise account in Desktop pointing to `https://ghe.evil-or-compromised.example`, and clones/opens a repository `acme/secret-project` tracked against that endpoint, with `origin` = `https://ghe.evil-or-compromised.example/acme/secret-project.git`.
2. The (compromised or malicious) GHE server responds to `GET repos/acme/secret-project` with a payload whose `clone_url` is `https://attacker-collector.example/acme/secret-project.git` (same scheme, different host/owner) [10](#0-9) .
3. On the next background repository refresh — e.g. `_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository` (triggered simply by opening or re-selecting the repo, or by the periodic repository-indicator refresh) — `updateRemoteUrl` is invoked with this `apiRepo` [11](#0-10) [1](#0-0) .
4. Because `protocolsMatch` is true, `remoteUrlUnchanged` is true (user never hand-edited `origin`), and `urlsMatch` is false (URL differs), `origin` is silently rewritten to `https://attacker-collector.example/acme/secret-project.git` [12](#0-11) .
5. The next time the user pushes from Desktop's UI, their commits (potentially containing private source code) are sent to `attacker-collector.example` instead of the intended repository, with no warning shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L2218-2257)
```typescript
  // finish `_selectRepository`s refresh tasks
  private async _selectRepositoryRefreshTasks(
    repository: Repository,
    previouslySelectedRepository: Repository | CloningRepository | null
  ): Promise<Repository | null> {
    this._refreshRepository(repository)

    if (isRepositoryWithGitHubRepository(repository)) {
      // Load issues from the upstream or fork depending
      // on workflow preferences.
      const ghRepo = getNonForkGitHubRepository(repository)

      this._refreshIssues(ghRepo)
      this.refreshMentionables(ghRepo)

      this.pullRequestCoordinator.getAllPullRequests(repository).then(prs => {
        this.onPullRequestChanged(repository, prs)
      })
    }

    // The selected repository could have changed while we were refreshing.
    if (this.selectedRepository !== repository) {
      return null
    }

    // "Clone in Desktop" from a cold start can trigger this twice, and
    // for edge cases where _selectRepository is re-entract, calling this here
    // ensures we clean up the existing background fetcher correctly (if set)
    this.stopBackgroundFetching()
    this.stopPullRequestUpdater()
    this.stopBackgroundPruner()

    this.startBackgroundFetching(repository, !previouslySelectedRepository)
    this.startPullRequestUpdater(repository)

    this.startBackgroundPruner(repository)

    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L4236-4246)
```typescript
  private getRepositoriesForIndicatorRefresh = () => {
    // The currently selected repository will get refreshed by both the
    // BackgroundFetcher and the refreshRepository call from the
    // focus event. No point in having the RepositoryIndicatorUpdater do
    // it as well.
    //
    // Note that this method should never leak the actual repositories
    // instance since that's a mutable array. We should always return
    // a copy.
    return this.repositories.filter(x => x !== this.selectedRepository)
  }
```

**File:** app/src/lib/stores/app-store.ts (L4258-4271)
```typescript
  private fetchForRepositoryIndicator(repo: Repository) {
    return this.withRefreshedGitHubRepository(repo, async repo => {
      const isBackgroundTask = true
      const gitStore = this.gitStoreCache.get(repo)

      await this.withPushPullFetch(repo, () =>
        gitStore.fetch(isBackgroundTask, progress =>
          this.updatePushPullFetchProgress(repo, progress)
        )
      )
      this.updatePushPullFetchProgress(repo, null)

      return gitStore.aheadBehind
    })
```

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
```typescript

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

**File:** app/src/lib/stores/app-store.ts (L8285-8306)
```typescript
  private async withRefreshedGitHubRepository<T>(
    repository: Repository,
    fn: (repository: Repository) => Promise<T>
  ): Promise<T> {
    let updatedRepository = repository
    const account: Account | null = getAccountForRepository(
      this.accounts,
      updatedRepository
    )

    // If we don't have a user association, it might be because we haven't yet
    // tried to associate the repository with a GitHub repository, or that
    // association is out of date. So try again before we bail on providing an
    // authenticating user.
    if (!account) {
      updatedRepository = await this.repositoryWithRefreshedGitHubRepository(
        repository
      )
    }

    return fn(updatedRepository)
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

**File:** app/src/lib/repository-matching.ts (L90-118)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
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
