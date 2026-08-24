### Title
Repository identity is tracked by mutable `owner/name` string instead of GitHub's immutable repository ID, allowing silent remote hijack via repo-jacking - (File: `app/src/lib/stores/updates/update-remote-url.ts`, `app/src/lib/repository-matching.ts`, `app/src/lib/stores/repositories-store.ts`)

### Summary
Like the Unitas oracle, which is blindly trusted the instant a new value is published without checking that it is still describing the same economic state, GitHub Desktop blindly trusts whatever GitHub API object comes back for an `owner/name` pair without checking that it is still the *same* repository (GitHub's immutable numeric repo `id`). Every time Desktop refreshes a repository — including immediately before a `push` — it re-fetches the repo by `owner+name`, compares the returned `clone_url` only against the previous *string* match, and, if it "matches", silently rewrites the local git remote and repository metadata with no user confirmation.

### Finding Description
`repositoryWithRefreshedGitHubRepository` re-derives `owner`/`name` from the current git remote via `matchGitHubRepository`, calls `api.fetchRepository(owner, name)`, and feeds the result straight into `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` decides whether to call `gitStore.setRemoteURL(...)` purely based on string comparisons of hostname/owner/name (`urlMatchesRemote`), with no verification that the API object still refers to the same underlying repository: [2](#0-1) 

The matching primitives used throughout (`repositoryMatchesRemote`, `urlMatchesRemote`) only ever compare `hostname`/`owner`/`name` strings — never GitHub's immutable numeric repository id: [3](#0-2) 

The persistence layer inherits the same weak identity: `_upsertGitHubRepository` looks up existing records by the compound key `[ownerID+name]`, and the stored `IDatabaseGitHubRepository` record has no field for GitHub's numeric repo id at all — only `ownerID`, `name`, `cloneURL`, `htmlURL`, `permissions`, `parentID`: [4](#0-3) 

On GitHub, when a repository is renamed or transferred, its old `owner/name` slug becomes available for **anyone** to claim with a brand-new, unrelated repository ("repo-jacking"). Because Desktop's entire repository-identity model is the mutable `owner/name` string (never the stable numeric id), a repo re-registered at a previously-used slug is indistinguishable, from Desktop's point of view, from the original repository. This is the same broken invariant as the oracle bug: a value that is expected to represent continuous identity/state (price, or here "which repo `origin` points to") is trusted immediately upon being fetched, with no check that its identity/continuity has been preserved since the last observation.

### Impact Explanation
`repositoryWithRefreshedGitHubRepository` runs on repository selection, background fetch, and — critically — is invoked directly inside `_push` before every push: [5](#0-4) 

If an attacker (an org member/repo owner who renames a repo, or anyone who claims a freed `owner/name` slug after a rename/transfer) causes the `owner/name` that a victim's `origin` remote still points at to resolve to an attacker-controlled repository, Desktop will:
1. Upsert the attacker's repo metadata (`cloneURL`, permissions, parent, pull requests, issues, branch protection) into the local `GitHubRepository` record as if it were the tracked project, without any user prompt.
2. Under the `protocolsMatch && remoteUrlUnchanged && !urlsMatch` condition, silently call `setRemoteURL` and repoint the git remote used for the very push about to occur.

The net effect is that a user's next push can be silently redirected to send their commits (proprietary source code) into a repository the attacker controls and can read — a form of silent corruption of what the user pushes, and of the destination they believe they are pushing to. It can also leak pull-request/branch-protection state from the attacker's repo into the UI, or (in the simpler case where the slug/URL doesn't even change) simply have Desktop keep treating the impostor repo as trusted for further metadata operations.

### Likelihood Explanation
This does not require local access, malware, leaked credentials, or unnatural user steps — it only requires the attacker to control a GitHub API object (a repository they own/admin, or one they register at a freed slug), which is explicitly in scope. The refresh-before-push code path executes automatically and silently on every push, with no user-visible confirmation dialog, so exploitation needs no cooperation from the victim beyond continuing to use Desktop normally against a repository whose `owner/name` has changed hands. Real-world "repo-jacking" of renamed/transferred GitHub repos is a well-documented technique, making the precondition realistic. The main mitigating factor is that the attacker must be able to claim the exact freed `owner/name` slug, which is opportunistic rather than always available, keeping this to medium-likelihood rather than trivially always-exploitable.

### Recommendation
Store and validate GitHub's immutable numeric repository `id` (already returned in `IAPIRepository`/`IAPIFullRepository`) alongside `owner/name` in `IDatabaseGitHubRepository`, and require an `id` match — not just string `owner/name`/URL match — before silently rewriting a remote URL or treating a freshly-fetched API repository as the same tracked project. If the `id` differs from the last known one for that `owner/name`, surface a warning to the user instead of auto-updating.

### Proof of Concept
1. Victim clones `https://github.com/attacker-org/foo` in GitHub Desktop; Desktop persists a `GitHubRepository` record keyed by `(ownerID, "foo")` with the current `cloneURL`.
2. Attacker (owner/admin of `attacker-org`) renames `foo` to `foo-archived`, freeing the `foo` slug, and creates a brand-new empty repository also named `foo` under their control.
3. Victim continues working locally; their local git remote `origin` is still `https://github.com/attacker-org/foo.git` (unchanged).
4. Victim triggers a push (`_push`), which calls `withRefreshedGitHubRepository` → `repositoryWithRefreshedGitHubRepository` → `api.fetchRepository('attacker-org', 'foo')`, returning the attacker's *new* repo object.
5. `urlMatchesRemote`/`updateRemoteUrl` compare only owner/name/hostname strings, find everything "matches" (nothing actually changed at the string level), so Desktop proceeds and pushes the victim's commits straight into the attacker's newly-created repository — with no numeric-id check ever performed and no prompt shown to the victim.

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

**File:** app/src/lib/stores/app-store.ts (L5155-5162)
```typescript
  public async _push(
    repository: Repository,
    options?: PushOptions
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPush(repository, options)
    })
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

**File:** app/src/lib/repository-matching.ts (L73-118)
```typescript
export function repositoryMatchesRemote(
  gitHubRepository: GitHubRepository,
  remote: IRemote
): boolean {
  return (
    urlMatchesRemote(gitHubRepository.htmlURL, remote) ||
    urlMatchesRemote(gitHubRepository.cloneURL, remote)
  )
}

/**
 * Check whether or not a GitHub repository URL matches a given remote, by
 * parsing and comparing the structure of the each URL.
 *
 * @param url a URL associated with the GitHub repository
 * @param remote the remote details found in the Git repository
 */
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
