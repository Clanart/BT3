### Title
Automatic remote-URL rewrite trusts GitHub API `clone_url` by name-match only, allowing silent origin hijack on repo/owner rename-squatting - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` silently rewrites the user's local `origin` remote URL whenever a periodic refresh detects that the GitHub API's `clone_url` for the matched `owner/name` differs from the currently configured remote, based purely on string-level owner/hostname/name matching rather than any repository-identity check (e.g. numeric repo id). This mirrors the C4 finding's root cause — a "parameter update" (the GitHub-reported clone URL) is propagated into another subsystem's state (the local git config) without validating that the two objects still refer to the same underlying entity.

### Finding Description
When Desktop refreshes a repository (`_refreshRepository` → `repositoryWithRefreshedGitHubRepository`), it derives `owner`/`name` by parsing the *existing* local remote URL via `matchGitHubRepository`: [1](#0-0) 

It then calls the GitHub API for that `owner`/`name` and, if the repo previously had `gitHubRepository` info attached, feeds the result straight into `updateRemoteUrl`: [2](#0-1) 

`updateRemoteUrl` decides to overwrite the local remote automatically if the protocol still matches and the *current* remote is judged "unchanged" relative to the previously cached `cloneURL`: [3](#0-2) 

The "unchanged" check (`urlMatchesRemote`) and the final `urlsMatch` check only compare `hostname` + `owner` + `name` strings (case-insensitively) — never a stable repository identifier such as GitHub's numeric repo id: [4](#0-3) 

Because the owner/name pair used to query the API is re-derived from the *current* remote on every refresh (not pinned to the originally-matched repository identity), any situation where the same `owner/name` slot on the configured `endpoint` starts returning a different `clone_url` (e.g. a GitHub username/org rename freeing the slot, followed by an attacker re-registering `owner` and creating a repo called `name` — a well known "repo/account rename-squatting" pattern — or a compromised/malicious GitHub Enterprise Server endpoint returning attacker-controlled `clone_url` values for that path) causes Desktop to call `gitStore.setRemoteURL(...)`, which directly executes `git remote set-url`: [5](#0-4) 

There is no user prompt, banner, or confirmation before this happens — the rewrite occurs silently during a background refresh, including one triggered simply by an account-store update: [6](#0-5) 

### Impact Explanation
Once `origin`'s URL is silently repointed to an attacker-controlled repository at the same hostname/owner/name, all subsequent `git push` operations from Desktop transparently upload the user's future commits (and, if using a credential helper against that same host, implicitly authorize the attacker's endpoint to receive the git-over-HTTPS session/credentials for that push) to a repository the victim did not choose. This is a silent corruption of what the user pushes, achieved purely through data the attacker controls (a GitHub API repository object returned for a name they've claimed, or a malicious/compromised GHE server), with no local access, admin rights, or social engineering step required beyond the routine repo-rename/re-registration race that GitHub itself permits.

### Likelihood Explanation
Likelihood is constrained by needing a specific precondition: the attacker must get a GitHub API response for the exact `owner/name` pair the victim's remote currently resolves to, with a `clone_url` that differs from the cached one but keeps the same owner/name/hostname tuple (satisfying `urlsMatch`'s string-only equality) — this is realistic mainly in the classic "abandoned username/repo re-registration" (repojacking) scenario, or when the account is pointed at a GitHub Enterprise Server endpoint that is attacker-controlled/compromised (explicitly an accepted primitive: "a GitHub API object" the attacker controls). It does not require MITM of TLS. The refresh path runs automatically and periodically for any repository with an associated `gitHubRepository`, so no unusual user action is needed to trigger the check.

### Recommendation
Do not use owner/hostname/name string equality as the sole trust anchor for automatically rewriting a local git remote. Before calling `gitStore.setRemoteURL`, verify that the API-returned repository is the *same* entity previously associated with the local `GitHubRepository` record by comparing GitHub's immutable numeric repository id (already stored via `upsertGitHubRepository`), not just the mutable owner/name/hostname strings. If the id differs, treat it as a different repository and require explicit user confirmation (e.g. a dialog) before changing `origin`'s URL, rather than silently invoking `setRemoteURL`.

### Proof of Concept
1. Victim has Desktop cloned repo with `origin` = `https://github.com/alice/project.git`, associated in Desktop's DB with `GitHubRepository` (`endpoint`, `cloneURL`, and an internal id) for the real `alice/project` repo.
2. `alice` renames her GitHub account (or the repository), freeing the `alice/project` owner/name slot on github.com.
3. Attacker registers the username `alice` and creates a public repository named `project` (or, for the GHE variant, the endpoint being queried is a malicious/compromised Enterprise Server that answers for `alice/project`).
4. On the next periodic `_refreshRepository` call (`app/src/lib/stores/app-store.ts:4048`), Desktop re-parses the still-configured `origin` URL to get `owner="alice"`, `name="project"` (`app-store.ts:4964-4977`), queries `api.fetchRepository('alice','project')`, and receives the attacker's `apiRepo` with `clone_url` (potentially still `https://github.com/alice/project.git` — identical string, so no rewrite needed) or a redirect-following variant with a different `clone_url` that still passes `urlMatchesRemote`'s owner/name/hostname check.
5. `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts:7-45`) determines `protocolsMatch && remoteUrlUnchanged && !urlsMatch` and calls `gitStore.setRemoteURL(...)`, rewriting `.git/config`'s `origin` URL with no prompt, or — even without any URL literal change — Desktop's cached `GitHubRepository` metadata (permissions, private flag, parent) is silently swapped to the attacker's repo object via `upsertGitHubRepository`/`setGitHubRepository` (`app-store.ts:4909-4910`), and the next `git push` from Desktop delivers the victim's commits to the attacker-controlled repository.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L4916-4933)
```typescript
  /**
   * Refreshes the GitHub repository information for the currently selected
   * repository when the active account changes. This ensures that permission
   * information is updated after signing in/out.
   */
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
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
