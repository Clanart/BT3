## Analysis

The Hats/Penrose finding is essentially a **stale-registration invariant break**: `unregisterContract()` fails to fully invalidate a market’s bookkeeping (`isOriginRegistered`, `clonesOf`), so state that should represent "no longer trusted" keeps pointing at data associated with a different/absent entity, and later logic (re-registration, iteration over `clonesOf`) acts on the stale, uncleared association.

The closest reachable analog in GitHub Desktop is in the `RepositoriesStore` GitHub-repository "matching"/upsert logic, which keys the locally-cached `GitHubRepository` record on a **mutable identifier pair** (`ownerID + name`, where `ownerID` is itself resolved via a case-insensitive `endpoint+login` key) instead of GitHub's immutable numeric repository ID, and which never invalidates that association when the underlying repository is deleted/renamed/transferred. This state is subsequently used to silently rewrite the user's local git remote. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Desktop's own code even documents that it never clears these GitHub repository associations (referencing `desktop/desktop#1144`), which is analogous to Penrose never clearing `isOriginRegistered`/`clonesOf` on unregister: [6](#0-5) 

### Title
Stale owner/name-keyed GitHub repository association is silently reused and drives automatic remote-URL rewriting - (File: app/src/lib/stores/repositories-store.ts, app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`RepositoriesStore` caches GitHub repository metadata keyed by `[ownerID+name]`, where `ownerID` is resolved from a case-insensitive `endpoint+login` key rather than GitHub's immutable numeric owner/repo IDs. When Desktop refreshes a tracked repository's GitHub association (`repositoryWithRefreshedGitHubRepository`), it re-derives owner/name from the local git remote, looks up (or reuses) the existing DB record for that owner/name, and overwrites `cloneURL`/`htmlURL`/permissions with whatever the API returns for that name today. `updateRemoteUrl` then silently runs `git remote set-url` to match the freshly-fetched `clone_url`, with no invalidation step for the case where the identity behind that login/name pair has changed.

### Finding Description
`putOwner` resolves an `Owner` id by a case-insensitive `[endpoint+login]` key and reuses the same local `ownerID` for any account matching that login string, even after the underlying GitHub account's identity changes (e.g. a login is renamed/released and claimed by someone else). `_upsertGitHubRepository`/`upsertGitHubRepositoryFromMatch` then look up an "existing" `GitHubRepository` DB row by `[ownerID+name]` and unconditionally overwrite it with the newly fetched API data if anything differs, never checking that this is still the "same" repository (there is no comparison against GitHub's stable numeric repo `id`). This is the same class of bug as `unregisterContract()`/`addOriginsMarket()`: an entity's on-chain/local record is looked up by a mutable, attacker-influenceable key (owner login + name) instead of a stable identifier, and there is no invalidation path once the underlying real-world binding changes.

Once the DB record is overwritten, `repositoryWithRefreshedGitHubRepository` calls `updateRemoteUrl`, which compares the *previous* cached `gitHubRepository.cloneURL` (now already the attacker's new value from the same overwritten record) to the local git remote and, if the local remote still matches the old cached URL and the protocol is unchanged, calls `gitStore.setRemoteURL(...)` to rewrite the repository's remote to the new `clone_url` — with no user confirmation.

### Impact Explanation
If the login associated with a tracked repository's owner is renamed away and reused by a different GitHub account (username squatting), or if the same owner deletes/recreates a repository under an identical name pointing at different content, Desktop will: (1) silently reuse the stale local `ownerID`/`GitHubRepository` row for the new, unrelated repository, and (2) automatically rewrite the user's local git `origin` remote URL to the new repository's `clone_url` without any prompt. This corrupts the destination of future `git push`/`git fetch` operations performed by the user — pushes intended for the original project can silently go to an attacker-controlled repository, and subsequent fetches/pulls can pull attacker-supplied history into the user's working repository. This matches the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
This requires no local access or malware: it only requires that a GitHub login or (owner, name) pair that a Desktop user has previously tracked becomes bound to different, attacker-controlled content (login squatting after rename/deletion, or a delete+recreate under the same name), which is an event fully within the "GitHub API object" attacker model. The refresh path (`repositoryWithRefreshedGitHubRepository`) runs during normal repository refresh/branch-protection flows, so no unusual user action is needed beyond Desktop periodically re-syncing metadata for previously tracked repositories.

### Recommendation
Key the local `GitHubRepository` cache and its owner/permission caches on GitHub's stable numeric repository/owner `id` rather than `[ownerID+name]`/`[endpoint+login]`. When refreshing a tracked repository, verify the API-returned `id` matches the previously stored `dbID`/owner id before reusing or overwriting cached metadata; if it doesn't match, treat it as a new/different repository and require explicit user confirmation before rewriting the local remote URL rather than calling `setRemoteURL` automatically.

### Proof of Concept
1. User adds and tracks `github.com/alice/project` in Desktop; Desktop stores `Owner{login: "alice", endpoint}` and `GitHubRepository{ownerID, name: "project", cloneURL: .../alice/project.git}`.
2. `alice` renames her GitHub login to `alice2`; GitHub allows `mallory` to claim the now-free `alice` login and creates a new public repository `alice/project`.
3. On the next background refresh, `repositoryWithRefreshedGitHubRepository` re-derives `owner=alice, name=project` from the local remote (still `.../alice/project.git`), calls `putOwner('https://api.github.com', 'alice')`, which returns the **same** local `ownerID` as before (case-insensitive login-key match), and `_upsertGitHubRepository` finds the existing `[ownerID+name]` row and overwrites its `cloneURL`/`htmlURL` with mallory's repository data. [2](#0-1) 
4. `updateRemoteUrl` compares the local remote (unchanged, matches the old cached `cloneURL`) against the (already-overwritten) `gitHubRepository.cloneURL`; since they now differ, and protocol matches, it calls `gitStore.setRemoteURL(...)`, silently repointing the user's `origin` remote to mallory's repository. [7](#0-6) 
5. The user's next `git push` sends their commits to mallory's repository instead of alice's.

### Citations

**File:** app/src/lib/databases/repositories-database.ts (L7-17)
```typescript
export interface IDatabaseOwner {
  readonly id?: number
  /**
   * A case-insensitive lookup key which uniquely identifies a particular
   * user on a particular endpoint. See getOwnerKey for more information.
   */
  readonly key: string
  readonly login: string
  readonly endpoint: string
  readonly type?: GitHubAccountType
}
```

**File:** app/src/lib/stores/repositories-store.ts (L494-528)
```typescript
  private async putOwner(
    endpoint: string,
    login: string,
    ownerType?: GitHubAccountType
  ): Promise<Owner> {
    const key = getOwnerKey(endpoint, login)
    const existingOwner = await this.db.owners.get({ key })
    let id

    // Since we look up the owner based on a key which is the product of the
    // lowercased endpoint and login we know that we've found our match but it's
    // possible that the case differs (i.e we found `usera` but the actual login
    // is `userA`). In that case we want to update our database to persist the
    // login with the proper case.
    if (
      existingOwner === undefined ||
      existingOwner.login !== login ||
      // This is added so that we update existing owners with an undefined type.
      (ownerType !== undefined && existingOwner.type !== ownerType)
    ) {
      id = existingOwner?.id
      const existingId = id !== undefined ? { id } : {}
      id = await this.db.owners.put({
        ...existingId,
        key,
        endpoint,
        login,
        type: ownerType,
      })
    } else {
      id = forceUnwrap('Missing owner id', existingOwner.id)
    }

    return new Owner(login, endpoint, id, ownerType ?? existingOwner?.type)
  }
```

**File:** app/src/lib/stores/repositories-store.ts (L613-679)
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

    if (existingRepo !== undefined) {
      // If nothing has changed since the last time we persisted the API info
      // we can skip writing to the database and (more importantly) avoid
      // telling store consumers that the repo store has changed.
      if (shallowEquals(existingRepo, updatedGitHubRepo)) {
        return this.toGitHubRepository(existingRepo, owner, parent)
      }
    }

    const id = await this.db.gitHubRepositories.put(updatedGitHubRepo)
    this.emitUpdatedRepositories()
    return this.toGitHubRepository({ ...updatedGitHubRepo, id }, owner, parent)
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

**File:** app/src/lib/stores/app-store.ts (L4874-4913)
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
```
