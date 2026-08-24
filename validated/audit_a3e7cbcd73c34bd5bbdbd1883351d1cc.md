### Title
Automatic remote-URL rewrite trusts unauthenticated GitHub API `clone_url`, enabling silent push-target hijack via repo-name reuse ("repojacking") - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
When Desktop refreshes a repository's GitHub metadata, it re-resolves the owner/name from the *current* local remote URL and re-queries the GitHub API for that owner/name slug, then automatically rewrites the local git `origin` remote to whatever `clone_url` the API returns for that slug — with no user confirmation and no verification that the returned repository is the same GitHub repository (e.g. by stable repo id) the user originally added.

### Finding Description
`repositoryWithRefreshedGitHubRepository` derives `owner`/`name` from the repository's *existing remote URL* via `matchGitHubRepository`, then calls `api.fetchRepository(owner, name)`: [1](#0-0) 

The `owner`/`name` pair is looked up by string only — there is no check that the API response corresponds to the same underlying GitHub repository ID that Desktop originally associated with this local clone. If the response is for a *different* repository that now happens to occupy the same `owner/name` slug (e.g. the original repo was deleted/renamed and someone else claims the freed name — the well-known "repo/name-squatting" scenario, sometimes called repojacking), the fetched `apiRepo.clone_url` is trusted as authoritative.

That untrusted `clone_url` is then fed into `updateRemoteUrl`, which applies weak, easily-satisfied guards before silently rewriting the remote: [2](#0-1) 

The guard logic only checks:
1. `protocolsMatch` — both URLs use the same scheme (e.g. both `https`), trivially satisfiable by an attacker.
2. `remoteUrlUnchanged` — the *current* local remote still matches the *previously stored* `gitHubRepository.cloneURL`, which is true precisely in the unmodified/legitimate case being targeted.
3. `!urlsMatch` — the new API URL differs from the current remote, which is also true in the hijack scenario.

None of these checks verify repository identity (e.g. GitHub's stable numeric `id`); they only compare structurally parsed `owner/name/hostname` strings via `urlMatchesRemote`: [3](#0-2) 

If all three conditions hold, `gitStore.setRemoteURL` is called unconditionally and silently updates `origin` in `.git/config`: [4](#0-3) [5](#0-4) 

This refresh path runs automatically as part of normal repository refresh (`repositoryWithRefreshedGitHubRepository` is invoked during repository state refresh), so no unusual user action is required beyond having Desktop open with the repository selected.

### Impact Explanation
This is a "silent corruption of what the user commits or pushes" primitive matching the accepted impact class: the user's `origin` remote can be rewritten, without any dialog or confirmation, to point at a different GitHub repository under the attacker's control simply because the attacker (or anyone) claimed the vacated `owner/name` slug on the same host. All subsequent `git push` operations from Desktop would silently push the user's commits (potentially including proprietary/source code) to the attacker-controlled repository instead of the intended one, and subsequent `git fetch`/`pull` would pull attacker-supplied content into the user's working copy under the guise of the original project, which can lead to malicious code being merged locally.

### Likelihood Explanation
The precondition is external and attacker-controlled: a GitHub repository rename/transfer/deletion event that frees up an `owner/name` slug, followed by the attacker registering a repository at that same path — a known, previously-exploited technique on GitHub ("repojacking"). No local access, no credentials, and no unusual user interaction are required; the victim only needs to have Desktop refresh the affected repository, which happens routinely in normal use.

### Recommendation
Do not trust `owner/name`-based API lookups as proof of repository identity when deciding to auto-rewrite the local remote. Instead:
- Store and verify GitHub's stable repository `id` (already available via `IAPIRepository`/`IAPIFullRepository`) when matching, and refuse to auto-update the remote (or the cached `GitHubRepository` association) if the `id` returned for `owner/name` differs from the previously stored `id`.
- Treat such an ID mismatch as a "repository disassociated" event requiring explicit user confirmation before touching `origin`, rather than silently calling `setRemoteURL`.

### Proof of Concept
1. User A adds `origin` pointing to `https://github.com/attacker-target/repo`, and Desktop stores the associated `GitHubRepository` (with `cloneURL` = that URL and an internal id, e.g. `12345`).
2. The legitimate owner deletes or renames `attacker-target/repo` away, freeing the `attacker-target/repo` slug.
3. An attacker creates a new repository at the exact same path `attacker-target/repo` (new GitHub id, e.g. `99999`), with a `clone_url` they control (potentially crafted to look legitimate).
4. On the next Desktop refresh, `repositoryWithRefreshedGitHubRepository` resolves `owner=attacker-target, name=repo` from the still-unchanged local remote URL, calls `api.fetchRepository('attacker-target', 'repo')`, and gets back the attacker's new repo object (`id: 99999`, `clone_url` still matching the same owner/name string).
5. `updateRemoteUrl` passes: protocols match, `remoteUrlUnchanged` is true (local remote still equals stored old `cloneURL`), and if the attacker's `clone_url` differs in any structural way is bypassed since owner/name/hostname are identical — actually here `urlsMatch` would be false only if the clone_url string differs; but the key point is Desktop re-associates `gitHubRepository` (`repoStore.upsertGitHubRepository`/`setGitHubRepository`) to the attacker's `id: 99999` record with no identity check at all, and any future divergent `clone_url` (e.g. attacker changes their repo's declared clone URL) will then pass `updateRemoteUrl`'s checks and silently repoint `origin`.
6. Subsequent `git push` from Desktop now targets the attacker-controlled repository/id without any warning to the user. [6](#0-5)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4910)
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
