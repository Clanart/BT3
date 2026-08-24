Based on my investigation, I found a plausible but only partially-verified analog. I was unable to fully trace all downstream consumers of `GitHubRepository.dbID` (e.g. whether `hasBranchProtectionsConfigured`'s cache miss on a newly-created dbID defaults to "unprotected" during the refresh window, or is awaited synchronously before push), so I present this with explicit caveats rather than full certainty.

### Title
Automatic git remote URL rewrite trusts GitHub API `clone_url` by name-only match, with no binding to the originally cloned repository identity - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop periodically refreshes the "linked" GitHub repository for a local clone and, under certain conditions, silently rewrites the local git remote URL to match whatever `clone_url` the GitHub API returns for the `owner/name` pair extracted from the existing remote string — with no verification against the repository's original stable identity.

### Finding Description
When Desktop refreshes a repository, `repositoryWithRefreshedGitHubRepository` calls `matchGitHubRepository` [1](#0-0)  which derives `owner`/`name` purely by string-parsing the current git remote URL, with no reference to any GitHub-assigned numeric repository ID. That `owner`/`name` pair is then used to call `api.fetchRepository(owner, name)` [2](#0-1) , and the returned `apiRepo` is fed into `updateRemoteUrl`, which will call `gitStore.setRemoteURL(...)` to rewrite the local `origin` remote whenever the previously-stored `GitHubRepository.cloneURL` still matches the current remote (i.e. the user hasn't manually customized it), the URL protocol is unchanged, and the API's `clone_url` differs from the current remote URL [3](#0-2) .

Persistence in `RepositoriesStore._upsertGitHubRepository` likewise keys `GitHubRepository` records by `[ownerID+name]`, where `ownerID` is derived from the owner *login string* via `putOwner` [4](#0-3) . There is no persisted stable GitHub repository ID used to confirm continuity — only the mutable owner-login/name pair is used as the identity key, and the fetched `clone_url` is written straight into the database and used to retarget the local remote [5](#0-4) .

This mirrors the structural flaw in the Truflation report: an operation intended to preserve continuity across an identity change (`migrateUser`/repo rename-or-ownership-change refresh) instead re-binds local state to a new identity using a mutable key (address/owner-login) rather than a stable one, and the old, no-longer-verified state is trusted implicitly going forward.

### Impact Explanation
If the `owner/name` slot that a user's remote currently points at ever resolves — even temporarily — to a different underlying repository than the one the user originally cloned (e.g. via GitHub-side rename churn, org handle reuse, or a race during a legitimate transfer), Desktop's background refresh will silently rewrite the user's local `origin` remote to the new `clone_url` with **no user confirmation dialog**. Subsequent `git push`/`git fetch` operations then transparently target a different remote than the one the user believes they're using, which is exactly the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
This requires a name/owner collision window on GitHub's side (rename, deletion+recreation, or ownership transfer) rather than any local/physical access, admin rights, or credential compromise — consistent with the required attacker model (attacker controls a GitHub API object). However, I could not confirm from local code how often or how easily such a collision window can be attacker-induced (vs. relying on GitHub's own uniqueness/reservation rules for repo names after rename/deletion), so likelihood is moderate/uncertain rather than confirmed.

### Recommendation
Bind `GitHubRepository` identity (and the auto-remote-rewrite decision in `updateRemoteUrl`) to GitHub's immutable numeric repository `id` rather than the mutable `owner login + name` pair, and require explicit user confirmation before silently changing a configured git remote URL.

### Proof of Concept
Not fully constructible from static analysis alone — it depends on being able to get GitHub's API to return a different `clone_url`/repository for the same `owner/name` string that Desktop already has associated with a local clone (e.g. via a rename/deletion/recreation race), which I could not verify is exploitable purely from the client-side code in this repository. I flag this as the strongest structural analog found, but recommend a Devin session with live GitHub API testing to confirm actual exploitability of the naming race before treating this as a confirmed vulnerability.

### Citations

**File:** app/src/lib/repository-matching.ts (L29-46)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-44)
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
```

**File:** app/src/lib/stores/repositories-store.ts (L610-616)
```typescript
    const { login, type } = gitHubRepository.owner
    const owner = await this.putOwner(endpoint, login, type)

    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()
```

**File:** app/src/lib/stores/repositories-store.ts (L654-666)
```typescript
    const updatedGitHubRepo: IDatabaseGitHubRepository = {
      ...(existingRepo?.id !== undefined && { id: existingRepo.id }),
      ownerID: owner.id,
      name: gitHubRepository.name,
      private: gitHubRepository.private,
      htmlURL: gitHubRepository.html_url,
      cloneURL: gitHubRepository.clone_url,
      parentID,
      lastPruneDate: existingRepo?.lastPruneDate ?? null,
      issuesEnabled: gitHubRepository.has_issues,
      isArchived: gitHubRepository.archived,
      permissions,
    }
```
