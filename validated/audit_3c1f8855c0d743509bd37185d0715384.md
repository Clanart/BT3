### Title
Silent, un-confirmed rewrite of a tracked repository's `origin` remote URL from stale GitHub API data - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
The Conic report's broken invariant is: "a change in the identity behind a stored reference silently zeroes/reassigns accumulated user-relevant state, with no user confirmation, based on data the invariant assumed was stable." The closest reachable analog in GitHub Desktop is `updateRemoteUrl`, which automatically overwrites a repository's `origin` remote URL whenever GitHub API data for the matched `owner/name` differs from what Desktop last saw — without any user prompt or verification that the new destination is the one the user intends to keep pushing/fetching to.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository`, which runs as part of the routine repository-selection refresh flow: [1](#0-0) 

It calls `api.fetchRepository(owner, name)` using `owner`/`name` resolved earlier from the locally stored `GitHubRepository` record, then unconditionally rewrites the local git remote if the API's `clone_url` differs from what's on disk and the protocol still matches: [2](#0-1) 

The only checks performed are:
1. that the local `origin` URL still equals the *previously recorded* `gitHubRepository.cloneURL` (`remoteUrlUnchanged`), and
2. that the URL scheme (`https:`/`ssh:`) hasn't changed (`protocolsMatch`).

Neither check verifies that the new `clone_url` still points to the same GitHub repository (by numeric id) that the user originally added. GitHub's rename/redirect semantics mean that `GET /repos/{owner}/{name}` can keep resolving successfully — returning a *different* `clone_url` — after the repository has been renamed or transferred to a different owner/name, because GitHub serves a redirect for the old `owner/name` pair. Since Desktop does the lookup by `owner`/`name` string rather than pinning to the GitHub repository `id`, the repository owner (an untrusted party from the perspective of a contributor who merely cloned/forked the project) can rename or transfer the tracked repo, and the very next time the user opens/selects that repository in Desktop, `git remote set-url origin <new-url>` is executed silently via `GitStore.setRemoteURL`: [3](#0-2) 

No dialog, diff, or confirmation is shown to the user before the remote is rewritten — this happens as a side effect of a background refresh (`_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository`), which is called automatically on every repository selection: [4](#0-3) 

### Impact Explanation
Because the remote rewrite happens transparently, the user's next `git push`/`git fetch` silently targets wherever the GitHub API currently reports for the (attacker-controlled) `owner/name` pair, rather than the destination the user originally trusted. This is a "silent corruption of what the user pushes" scenario: the destination of a push (and therefore who receives the code, and potentially what credentials/tokens are transmitted to that destination via the credential helper flow keyed by `endpoint`) can be altered by an action entirely under the control of the tracked repository's owner, without any Desktop-side confirmation UI. Existing guards (`protocolsMatch`, `remoteUrlUnchanged`) only protect against protocol downgrade and manually-customized remotes — they do nothing to verify the new URL still refers to the same underlying repository (by id), which is exactly the gap that GitHub's rename-redirect behavior exploits.

### Likelihood Explanation
The trigger path requires nothing from the victim beyond normal use of the app: opening/selecting a repository they already have added to Desktop that they don't administer (e.g., a contributor working against someone else's public/private repo). It requires the attacker to rename or transfer the repository they control — an ordinary, low-friction GitHub action, not any local/physical access, admin right on Desktop, or leaked credentials. This is well within the "attacker controls a GitHub API object" and "git remote/proxy response" categories called out as valid impact classes for this analysis.

### Recommendation
Pin the identity check to the immutable GitHub repository `id` (already tracked in `GitHubRepository`/`IAPIFullRepository`) instead of, or in addition to, the `owner/name` string match before silently rewriting the remote URL. If the `id` doesn't match the previously stored one, treat it as a different repository and either skip the auto-update or surface an explicit confirmation dialog to the user before calling `gitStore.setRemoteURL`.

### Proof of Concept
1. Victim adds/clones `github.com/attacker/project` in GitHub Desktop; Desktop stores `GitHubRepository` with `cloneURL = https://github.com/attacker/project`.
2. Attacker renames `attacker/project` to `attacker/project-2` (or transfers it to a different owner) on GitHub. GitHub keeps the old `owner/name` path resolvable via redirect at the API level.
3. Victim reopens Desktop or re-selects the repository in the sidebar, triggering `_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository` → `api.fetchRepository('attacker', 'project')`, which succeeds and returns the new `clone_url` for `attacker/project-2`.
4. `updateRemoteUrl` sees `remoteUrlUnchanged === true` (local origin still matches old stored `cloneURL`) and `protocolsMatch === true`, so it calls `gitStore.setRemoteURL('origin', 'https://github.com/attacker/project-2')` with no prompt shown to the victim.
5. The victim's local `origin` now silently points at `attacker/project-2`; subsequent pushes/fetches operate against the new location without the victim having consented to or even noticed the change. [5](#0-4)

### Citations

**File:** app/src/lib/stores/app-store.ts (L2218-2258)
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
  }
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
