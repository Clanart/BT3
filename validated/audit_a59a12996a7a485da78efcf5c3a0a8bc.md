### Title
Remote-owning `GitHubRepository` identity is re-derived from a mutable remote URL/owner-name string instead of a persisted immutable ID, allowing repo-rename/reclaim to poison local repo metadata and remotes - (File: `app/src/lib/stores/app-store.ts`)

### Summary
GitHub Desktop links a local clone to its "GitHub repository" record by re-parsing the current `origin` remote URL into `owner`/`name` on every refresh, then re-fetching and upserting whatever GitHub repository currently answers to that `owner/name`. There is no verification that the returned API object is the *same* repository (by immutable numeric ID) that Desktop originally associated with the clone. This mirrors the reported class of bug: an "address"/identity is recomputed from attacker-influenceable input and trusted without checking it came from the original, legitimate binding.

### Finding Description
`matchGitHubRepository` derives the owner/name purely from the on-disk git remote URL string: [1](#0-0) 

That `owner`/`name` pair is then used to fetch a fresh repository object straight from the GitHub API in `repositoryWithRefreshedGitHubRepository`: [2](#0-1) 

The result (`apiRepo`) is persisted via `upsertGitHubRepository`, which looks up the existing DB record purely by `[ownerID+name]` and overwrites its fields (`cloneURL`, `htmlURL`, `private`, `permissions`, `parentID`, etc.) if anything differs: [3](#0-2) 

Critically, the lookup key is the mutable `owner/name` pair, not the immutable numeric GitHub repository ID. On GitHub, an `owner/name` pair is not permanently bound to one repository: it can be freed and re-claimed after a rename, transfer, deletion, or the owner account/organization being renamed. When that happens, `api.fetchRepository(owner, name)` — which Desktop calls with the *stale* owner/name recovered from the local remote — will return an entirely different, attacker-controlled repository object, and Desktop will silently adopt its metadata as if it were the same trusted repository.

The companion helper `updateRemoteUrl` compounds the problem for the actual git remote: it decides whether to auto-rewrite `origin`'s URL using only string-based `urlMatchesRemote`/protocol comparisons, not any persisted identity check: [4](#0-3) 

Existing guards (`urlMatchesRemote`, `protocolsMatch`) only compare `hostname/owner/name` strings between two URLs — exactly the value an attacker controls once they reclaim the name — so they do not stop the substitution; they were designed to avoid *unwanted* protocol changes, not to authenticate repository identity.

### Impact Explanation
Once the attacker's repository is silently upserted as the local repo's `GitHubRepository`, several trust decisions downstream are poisoned without any user prompt:
- Branch-protection state is refreshed from the attacker's repository (`refreshBranchProtectionState`), potentially removing force-push warnings that normally protect the user (see `_publishRepository`/`repositoryWithRefreshedGitHubRepository` flow at [5](#0-4) ).
- `permissions`/`private` flags used elsewhere in the UI/CLI flow are now attacker-supplied.
- The `parent` field (fork upstream) from the attacker's API object is trusted and can be used to auto-configure an "upstream" remote for the repository, redirecting future fetches/pulls toward attacker infrastructure.
- Repository matching used for "Open in Desktop" / PR-checkout flows (`doesRepositoryMatchUrl`, `getRepositoryFromPullRequest`) relies on the same string-based `urlsMatch` comparison, so an attacker-reclaimed name can also cause Desktop to misassociate deep-link/PR actions with the wrong local clone: [6](#0-5) 

This falls in the accepted impact class of "silent corruption of what the user commits/pushes" via metadata/remote poisoning, and is triggered purely by GitHub API objects the attacker can shape (by owning a reclaimed `owner/name`), not by local/physical access or leaked credentials.

### Likelihood Explanation
Exploitation requires the attacker to control (or race to claim) an `owner/name` combination that a victim's Desktop repo used to point at — realistic in known "repo-jacking"/name-squatting scenarios (a repo/org gets renamed or deleted, freeing the slug, and an attacker immediately registers it). Desktop performs this refresh automatically and silently in the background (`_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository`, also triggered on every fetch/pull via `withRefreshedGitHubRepository`), so no user interaction beyond normal use of the app is required once the name has been reclaimed. This raises the likelihood above a purely theoretical race, though the precondition (needing a name to become reclaimable and reclaiming it before the legitimate re-registration or before the user notices) is nontrivial and time-sensitive, so likelihood is moderate rather than trivial.

### Recommendation
Bind the local repository's association to the GitHub repository by its immutable numeric `id` (already stored as `GitHubRepository.dbID`/API `id` field) rather than re-deriving and re-matching by `owner/name` on every refresh. Concretely:
- Persist and check the GitHub repository numeric ID when fetching/upserting (`api.fetchRepository` should ideally fetch by ID when one is already known, and `_upsertGitHubRepository`'s lookup key should include the immutable ID rather than `[ownerID+name]` alone).
- Before silently overwriting `cloneURL`/remote data, verify the returned API object's ID matches the previously recorded ID for that local repository; if it doesn't, treat it as a different repository and surface this to the user instead of auto-adopting the new metadata/remote.
- Apply the same ID-based verification to `urlsMatch`/`urlMatchesRemote`-based matching used for deep-link and pull-request association flows.

### Proof of Concept
1. Victim has a Desktop-tracked repository whose `origin` points to `https://github.com/foo/bar` and is associated with `GitHubRepository(id=1111, owner=foo, name=bar, ...)`.
2. The real `foo/bar` repository is renamed/transferred/deleted such that the `foo/bar` slug becomes available again (a documented GitHub condition, e.g. after org/user rename or repo deletion).
3. Attacker immediately registers a new repository at `foo/bar` (either recreating a user named `foo` with repo `bar`, or an org rename opportunity) with attacker-chosen `private`, `permissions`, `parent`, and `clone_url` fields.
4. Victim's Desktop performs a routine background refresh (`_fetch`, `_selectRepositoryRefreshTasks`, or simply reselecting the repository), calling `matchGitHubRepository` → `api.fetchRepository('foo', 'bar')`, which now returns the attacker's repository object.
5. `repositoryWithRefreshedGitHubRepository` upserts this object over the victim's existing `GitHubRepository` record (matched by `owner/name`) with no user confirmation, and evaluates `updateRemoteUrl` against it — silently changing branch-protection state, permissions, and potentially the `origin`/`upstream` remote configuration used for the victim's next fetch/pull/push.

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

**File:** app/src/lib/stores/repositories-store.ts (L613-666)
```typescript
    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()

    // If we can't resolve permissions for the current repository chances are
    // that it's because it's the parent repository of another repository and we
    // ended up here because the "actual" repository is trying to upsert its
    // parent. Since parent repository hashes don't include a permissions hash
    // and since it's possible that the user has both the fork and the parent
    // repositories in the app we don't want to overwrite the permissions hash
    // in the parent repository if we can help it or else we'll end up in a
    // perpetual race condition where updating the fork will clear the
    // permissions on the parent and updating the parent will reinstate them.
    const permissions =
      getPermissionsString(gitHubRepository) ??
      existingRepo?.permissions ??
      undefined

    // If we're told to ignore the parent then we'll attempt to use the existing
    // parent and if that fails set it to null. This happens when we want to
    // ensure we have a GitHubRepository record but we acquired the API data for
    // said repository from an API endpoint that doesn't include the parent
    // property like when loading pull requests. Similarly even when retrieving
    // a full API repository its parent won't be a full repo so we'll never know
    // if the parent of a repository has a parent (confusing, right?)
    //
    // We do all this to ensure that we only set the parent to null when we know
    // that it needs to be cleared. Otherwise we could have a scenario where
    // we've got a repository network where C is a fork of B and B is a fork of
    // A which is the root. If we attempt to upsert C without these checks in
    // place we'd wipe our knowledge of B being a fork of A.
    //
    // Since going from having a parent to not having a parent is incredibly
    // rare (deleting a forked repository and creating it from scratch again
    // with the same name or the parent getting deleted, etc) we assume that the
    // value we've got is valid until we're certain its not.
    const parentID = ignoreParent
      ? existingRepo?.parentID ?? null
      : parent?.dbID ?? null

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1920-1938)
```typescript
  private doesRepositoryMatchUrl(
    repo: Repository | CloningRepository,
    url: string
  ): repo is RepositoryWithGitHubRepository {
    if (repo instanceof Repository && isRepositoryWithGitHubRepository(repo)) {
      const originRepoUrl = repo.gitHubRepository.htmlURL
      const upstreamRepoUrl = repo.gitHubRepository.parent?.htmlURL ?? null

      if (originRepoUrl !== null && urlsMatch(originRepoUrl, url)) {
        return true
      }

      if (upstreamRepoUrl !== null && urlsMatch(upstreamRepoUrl, url)) {
        return true
      }
    }

    return false
  }
```
