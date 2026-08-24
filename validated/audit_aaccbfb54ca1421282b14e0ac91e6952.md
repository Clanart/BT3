### Title
Local GitHub repository identity/metadata can be silently overwritten by a same-named repository owned by a reused GitHub login - ([File: app/src/lib/stores/repositories-store.ts])

### Summary
The reported smart-contract bug is a class of "unstable/collidable identity used as the sole key for state overwrite": a DAO's identity is derived from a value (deployment nonce) that is not guaranteed unique across chains, letting an attacker with the same derived identity overwrite the victim's stored state. The same bug class exists in GitHub Desktop's `RepositoriesStore`: the local `GitHubRepository` database record's identity is keyed purely on the *mutable, attacker-obtainable* pair `[ownerID + name]` (owner login + repo name), never on GitHub's immutable numeric repository id. If a GitHub login is renamed/freed and re-registered by an attacker who creates a repository with the same name, Desktop's periodic repository-refresh logic will match the attacker's repository to the victim's existing local record and overwrite it in place.

### Finding Description
`RepositoriesStore._upsertGitHubRepository` looks up the "existing" `GitHubRepository` purely by owner id + repo name and then overwrites all of its API-derived fields: [1](#0-0) 

The `ownerID` itself is resolved by `putOwner`, which keys owners on `[endpoint+login]` (a lowercased login string), not on GitHub's numeric account id: [2](#0-1) 

The uniqueness constraint enforced by the database schema is explicitly `&[ownerID+name]` — i.e., "one repository per owner-login+name string", not per actual GitHub repository: [3](#0-2) 

The refresh path that triggers this upsert derives `owner`/`name` from the *git remote URL text* (not from any GitHub-side numeric id), via `matchGitHubRepository`/`parseRemote`, and then calls the GitHub API for that owner/name pair: [4](#0-3) [5](#0-4) 

Nowhere in this chain is GitHub's stable repository id (`IAPIRepository.id`/`node_id`) compared against the previously stored record before the fields are overwritten — the code only compares owner-login+name strings. `urlMatchesRemote`/`urlsMatch`, used elsewhere to decide "is this the same repository", likewise compare only hostname/owner/name text: [6](#0-5) 

Because GitHub allows a username to be renamed and, after the rename, the vacated login to be claimed by anyone (a long-documented "GitHub username squatting" scenario), an attacker can:
1. Wait for or induce the situation where the victim's GitHub login `owner` (whose repo `owner/repo` the victim has cloned in Desktop) is renamed or the account/repo removed.
2. Register the freed `owner` login and create a new repository named `repo`.
3. When Desktop's background repository refresh runs (`repositoryWithRefreshedGitHubRepository`), it resolves the same `owner`/`repo` pair, fetches the attacker's repository from the API, and calls `upsertGitHubRepository`, which matches the *existing* `GitHubRepository` record for the victim's local repo (same `[ownerID+name]` key) and overwrites its `cloneURL`, `htmlURL`, `parentID`, `permissions`, `private`, `issuesEnabled`, and `isArchived` fields with the attacker's data — all without ever creating a new record or alerting the user.
4. This corrupted `GitHubRepository` record is exactly the one associated (via `RepositoriesStore.setGitHubRepository`, `Repository.gitHubRepositoryID`) with the victim's untouched local clone.

### Impact Explanation
Once the victim's local repository record is silently repointed to the attacker's GitHub repository:
- `updateRemoteUrl` (called right after the upsert) decides whether to rewrite the local git remote URL by comparing the *old, now already-attacker-influenced* `gitHubRepository.cloneURL` against the current remote and the new `clone_url` returned by the API for the attacker's repo: [7](#0-6)  — if the remote hasn't been manually altered and the protocol matches, Desktop will overwrite the user's git `origin` remote URL with the attacker-controlled clone URL, so the next `git fetch`/`git pull`/`git push` talks to the attacker's repository/proxy.
- Even without the remote URL changing, downstream code trusts the corrupted `permissions`, `private`, and `parentID` fields (e.g. fork/parent-network resolution, PR-repository matching in `pull-request-coordinator.ts`'s `findRepositoriesForGitHubRepository`, which matches purely on `dbID`): [8](#0-7) . This can cause Desktop to display/act on pull requests, checkout targets, or "push access" affordances belonging to the attacker's repository as if they belonged to the victim's tracked repository.
- Net effect: an unprivileged party (someone who merely registers a freed GitHub username and creates a like-named repo) can cause silent corruption of what the Desktop user fetches from / pushes to, matching the "silent corruption of what the user commits or pushes" and "git remote/proxy response" impact categories.

### Likelihood Explanation
Medium. It requires (a) a GitHub login rename/release event for an account the victim has cloned a repo from, and (b) the attacker registering that login and creating a repository with the same name before the victim's next background refresh. GitHub explicitly permits login reuse after renames, and repository/owner renaming is common; Desktop performs this refresh automatically and periodically without any user action, so no unusual user steps are required once the login/name collision exists. No local access, admin rights, or pre-existing malware is needed — only ordinary use of GitHub's account/repo naming features.

### Recommendation
- Key `GitHubRepository` identity (and the Dexie `gitHubRepositories` uniqueness index) on GitHub's immutable numeric `id`/`node_id` (present in `IAPIRepository`) rather than on `[ownerID+name]`.
- Before overwriting an existing `GitHubRepository` record in `_upsertGitHubRepository`, verify that the API's repository id matches the previously stored id (if one is stored); if it differs, treat it as a new/foreign repository (create a new record or surface a warning to the user) instead of silently mutating the existing record.
- Likewise, harden `urlMatchesRemote`/`urlsMatch` and `updateRemoteUrl` to require agreement on a stable repository id before automatically rewriting the tracked remote URL.

### Proof of Concept
1. Victim clones `https://github.com/alice/project` in Desktop; Desktop stores a `GitHubRepository` row keyed `[ownerID(alice)+"project"]` with alice's real `cloneURL`/`permissions`.
2. Alice renames her GitHub account to `alice2` (or deletes it), freeing the `alice` login.
3. Attacker registers the `alice` login and creates a new repository also named `project` (`https://github.com/alice/project`, but a different underlying repo id).
4. Desktop's periodic background refresh (`AppStore._refreshRepository` → `repositoryWithRefreshedGitHubRepository` → `matchGitHubRepository`/`api.fetchRepository('alice','project')`) fetches the attacker's repository data and calls `repositoriesStore.upsertGitHubRepository(endpoint, apiRepo)`.
5. `_upsertGitHubRepository` finds the existing row via `.where('[ownerID+name]').equals([owner.id, 'project'])` (same key, because owner id is resolved by login string and both alice-the-original and alice-the-attacker map to the same stored owner row) and overwrites `cloneURL`, `htmlURL`, `parentID`, `permissions`, `private`, etc. with the attacker's values — with no comparison of any stable GitHub repository id.
6. Depending on the returned `clone_url` and whether `updateRemoteUrl`'s conditions hold, Desktop may silently rewrite the victim's `origin` remote to the attacker's clone URL, so the victim's next fetch/push interacts with the attacker's repository.

### Citations

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

**File:** app/src/lib/stores/repositories-store.ts (L596-666)
```typescript
  private async _upsertGitHubRepository(
    endpoint: string,
    gitHubRepository: IAPIRepository | IAPIFullRepository,
    ignoreParent = false
  ): Promise<GitHubRepository> {
    const parent =
      'parent' in gitHubRepository && gitHubRepository.parent !== undefined
        ? await this._upsertGitHubRepository(
            endpoint,
            gitHubRepository.parent,
            true
          )
        : await Promise.resolve(null) // Dexie gets confused if we return null

    const { login, type } = gitHubRepository.owner
    const owner = await this.putOwner(endpoint, login, type)

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

**File:** app/src/lib/databases/repositories-database.ts (L131-145)
```typescript
    this.conditionalVersion(4, {
      gitHubRepositories: '++id, name, &[ownerID+name]',
    })

    this.conditionalVersion(5, {
      gitHubRepositories: '++id, name, &[ownerID+name], cloneURL',
    })

    this.conditionalVersion(6, {
      protectedBranches: '[repoId+name], repoId',
    })

    this.conditionalVersion(7, {
      gitHubRepositories: '++id, &[ownerID+name]',
    })
```

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

**File:** app/src/lib/stores/pull-request-coordinator.ts (L263-272)
```typescript
function findRepositoriesForGitHubRepository(
  gitHubRepository: GitHubRepository,
  repositories: ReadonlyArray<RepositoryWithGitHubRepository>
): ReadonlyArray<RepositoryWithGitHubRepository> {
  const { dbID } = gitHubRepository

  return repositories.filter(
    repository => getNonForkGitHubRepository(repository).dbID === dbID
  )
}
```
