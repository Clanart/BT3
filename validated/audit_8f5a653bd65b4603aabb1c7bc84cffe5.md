### Title
Silent remote-URL takeover via owner/name-only repository matching (repo-jacking) — ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically rewrites a repository's local `origin` remote URL whenever GitHub Desktop refreshes the associated GitHub repository (before every push/pull/fetch). The decision to trust and apply the new URL is based entirely on string matching of `{hostname, owner, name}` — never on a persistent GitHub repository identifier. Because GitHub allows repository names to be freed and re-claimed (rename/delete/transfer, i.e. classic "repo-jacking"), an unprivileged attacker who registers a repository under the same `owner/name` that a victim previously tracked can cause Desktop to silently re-point the victim's `origin` remote at the attacker's repository, corrupting the destination of future pushes without any user prompt.

### Finding Description
`repositoryWithRefreshedGitHubRepository` (app/src/lib/stores/app-store.ts, ~4874-4914) resolves the tracked repo via `matchGitHubRepository`, which derives `owner`/`name` purely by regex-parsing the local remote URL string and matching only the account's endpoint hostname: [1](#0-0) 

It then calls the GitHub API using only these strings (`api.fetchRepository(owner, name)`): [2](#0-1) 

The returned `apiRepo` (an attacker-controllable GitHub API object once the attacker owns that owner/name) is passed to `updateRemoteUrl`, which decides whether to overwrite the local git remote: [3](#0-2) 

The guard conditions are:
- `protocolsMatch` — only checks that the URL scheme (https vs ssh) is unchanged.
- `remoteUrlUnchanged` — checks that the *previously stored* `gitHubRepository.cloneURL` still matches the local remote via `urlMatchesRemote`, which itself only compares `hostname`/`owner`/`name` strings, never a numeric repository id: [4](#0-3) 

None of these checks reference the GitHub repository's immutable numeric `id`. So if the underlying repository at `owner/name` changes ownership (deleted/renamed and re-created by someone else, or an org member frees a name that an outside attacker then claims), the new `clone_url` returned by the API will satisfy `protocolsMatch && remoteUrlUnchanged && !urlsMatch` and Desktop will call `gitStore.setRemoteURL(...)` to silently repoint `origin` at the attacker's repository.

This refresh path is invoked automatically, with no user confirmation, on every push, pull, and fetch via `withRefreshedGitHubRepository` (used by `_push`, fetch, and pull flows): [5](#0-4) [6](#0-5) 

### Impact Explanation
The corrupted value is the git remote URL (`gitStore.defaultRemote.url`, i.e., the `origin` entry in the local `.git/config`). Once silently rewritten to the attacker's repository, the victim's subsequent `git push` operations — issued transparently through Desktop's normal UI push button — will upload commits, branches, and potentially credentials-scoped tokens (via the trampoline credential helper, which authenticates against whatever host/endpoint the remote points to) to a repository fully controlled by the attacker. This is a silent corruption of what the user pushes, matching the "attacker controls ... a GitHub API object" and "silent corruption of what the user commits or pushes" impact classes.

### Likelihood Explanation
Requires no local access, no admin privileges, and no prior credential leak: an unprivileged GitHub user simply needs to re-register a repository at an `owner/name` combination previously used by a repository a victim already has cloned in Desktop (a well-known "repo-jacking" pattern that GitHub itself has had to mitigate at points for popular package ecosystems). No user interaction beyond normal Desktop usage (push/pull/fetch) is required once the substitution exists — the rewrite happens automatically in the background refresh path.

### Recommendation
Do not trust `owner`/`name` string equality alone as proof of repository identity across refreshes. Persist and compare the immutable GitHub repository `id` (already available on `IAPIRepository`/`IAPIFullRepository` payloads) between the previously stored `GitHubRepository` record and the freshly fetched `apiRepo` before allowing `updateRemoteUrl` to rewrite the local remote. If the `id` differs from what was last associated with that `owner/name`, treat it as a different repository, refuse the silent auto-update, and instead surface a clear warning/prompt to the user before changing the push/fetch destination.

### Proof of Concept
1. Victim clones `https://github.com/acme/webapp.git` in GitHub Desktop; Desktop stores a `GitHubRepository` DB record with `cloneURL` pointing at `acme/webapp` (its API `id` is irrelevant to later checks).
2. The `acme/webapp` repository is later deleted or renamed (e.g., org restructuring), freeing the `owner/name` slot.
3. Attacker (unprivileged, no relation to the victim) creates a *new* repository also named `webapp` under `acme` (if org policy permits) or reuses an owner slug that becomes available, with `clone_url` pointing at attacker infrastructure/repo.
4. Victim performs a normal `git push` from Desktop. `_push` → `withRefreshedGitHubRepository` → `repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository('acme', 'webapp')`, which now returns the attacker's repo data.
5. `updateRemoteUrl` sees `protocolsMatch = true`, `remoteUrlUnchanged = true` (owner/name/hostname strings still match the stored DB record), `urlsMatch = false` (clone_url differs), and calls `gitStore.setRemoteURL('origin', attackerCloneUrl)` — silently rewriting the local `origin` remote.
6. The victim's push (and future pushes) now go to the attacker's repository without any prompt or visible warning in the push flow.

**Uncertainty:** I could not fully trace whether other UI-level confirmations (e.g. a toast/notification on remote change) exist elsewhere in the codebase outside the indexed snippets; the index size limits mean some surrounding UI-notification code may not have been retrieved. If such a user-facing confirmation exists elsewhere and is not bypassable, it would reduce (but not eliminate, since the rewrite itself still occurs before any explicit consent) the severity of this finding. Starting a full Devin session against the complete repository would allow verifying this end-to-end.

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

**File:** app/src/lib/stores/app-store.ts (L5895-5899)
```typescript
  public _fetch(repository: Repository, fetchType: FetchType): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType)
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
