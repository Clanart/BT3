## Title
Silent, unconfirmed remote-URL rewrite driven by an attacker-controlled GitHub API response can redirect a user's future pushes - (`File: app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The Sherlock report describes a stale-authorization bug: `buyoutLien()` transfers lien ownership but leaves `lienData[lienId].payee` unchanged, so a party that no longer owns the asset keeps silently receiving value until the new owner notices and resets it. The Desktop analog is `updateRemoteUrl()`, which is invoked on essentially every fetch/pull/push cycle and will silently rewrite the local `origin` remote to whatever `clone_url` the (attacker-influenceable) GitHub API response contains, based on weak, identity-free string-matching guards, with no user confirmation.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` re-resolves the repository's owner/name purely from the existing git remote URL via `matchGitHubRepository()` [1](#0-0) , then calls `api.fetchRepository(owner, name)` and immediately calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)` [2](#0-1) .

`updateRemoteUrl()` then decides, without any user interaction, whether to rewrite the local remote: [3](#0-2) 

The only guards are:
- `protocolsMatch` — compares just the URL *scheme* (`https:` vs `ssh:`), never the hostname.
- `remoteUrlUnchanged` — compares the current remote against the *previously cached* `gitHubRepository.cloneURL`, via `urlMatchesRemote()`, which only compares `hostname`/`owner`/`name` strings [4](#0-3) .

Neither guard verifies the *identity* (numeric repository `id`) of the repository being returned by the API — they trust whatever `clone_url` the API/server hands back. If that condition set is satisfied, Desktop executes `gitStore.setRemoteURL(...)` [5](#0-4) , which runs `git remote set-url <name> <url>` directly against the repository [6](#0-5) .

This code path is reached from ordinary background operations, not just explicit user actions — `withRefreshedGitHubRepository()`/`repositoryWithRefreshedGitHubRepository()` is called from fetch, pull, and push flows (`_fetch`, `_pull`, `performPush`, and the background repository-indicator fetcher) [7](#0-6) , so the rewrite can happen entirely in the background while the user is simply working.

The trust root here is the GitHub API response object (`IAPIFullRepository`/`apiRepo.clone_url`), which the report's threat model explicitly allows as attacker-controlled (e.g., a compromised/malicious GitHub Enterprise Server the account is configured against, or a MITM'd/malicious proxy in front of it). Because Desktop never pins the remote to a stable repository identifier, a server capable of answering `fetchRepository(owner, name)` untruthfully can hand back an arbitrary `clone_url` (any host, as long as the protocol scheme matches) and Desktop will splice it into the user's git config as the new `origin`.

### Impact Explanation
Once `origin` is silently repointed, the corrupted value is exactly the kind of "silent corruption of what the user commits/pushes" called out as valid impact: the next `git push` (and any subsequent fetch/pull) transparently goes to the attacker-chosen destination instead of the user's real repository, with no diff or confirmation dialog surfaced (repository settings would show the new URL only if the user manually checks). Depending on the substituted host, this can also cause Desktop's credential/trampoline layer to hand out credentials scoped to that new host (`findGenericTrampolineAccount`/generic credential prompts) [8](#0-7) , extending the impact from "lost pushes" toward credential exposure to the attacker's endpoint.

### Likelihood Explanation
This requires the account in question to be configured against a GitHub Enterprise Server (or generic Git host reachable through a network path) that is attacker-controlled or compromised, or a MITM position on that traffic — no local access, no admin rights, and no unusual user action are needed; the rewrite happens automatically the next time Desktop performs a background fetch/pull/push against that repository. The existing guards (`protocolsMatch`, `remoteUrlUnchanged`) do nothing to stop this because they never validate the destination's identity, only superficial string shape.

### Recommendation
Before calling `setRemoteURL()`, `updateRemoteUrl()` should validate the returned `clone_url` against a stable identifier of the previously known repository (e.g., compare `apiRepo.id`/`node_id` against the cached `GitHubRepository`'s id, not just owner/name strings), and/or require explicit user confirmation before rewriting an existing remote to a different host, surfacing a warning similar to SSH host-key changes rather than silently mutating git config.

### Proof of Concept
1. User adds/uses a GitHub Enterprise account whose endpoint is later compromised, MITM'd, or otherwise able to return arbitrary API JSON (e.g., malicious/rogue GHES instance).
2. User has a repository cloned normally, with `origin` pointing at `https://ghes.example.com/acme/webapp.git`, matching the cached `GitHubRepository.cloneURL`.
3. Desktop performs a routine fetch/pull/push, triggering `repositoryWithRefreshedGitHubRepository()` → `api.fetchRepository('acme', 'webapp')`.
4. The compromised/malicious server responds with `clone_url: "https://ghes.example.com/attacker/webapp.git"` (same protocol, so `protocolsMatch` is true; `remoteUrlUnchanged` is true because the user hasn't manually edited the remote; `urlsMatch` is false because owner differs).
5. `updateRemoteUrl()` calls `gitStore.setRemoteURL('origin', 'https://ghes.example.com/attacker/webapp.git')`, silently rewriting the user's git config — no dialog, no diff.
6. The user's next `git push` sends commits (and possibly credentials via the credential helper for that host) to the attacker's repository instead of `acme/webapp`.

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

**File:** app/src/lib/trampoline/find-account.ts (L31-60)
```typescript
export async function findGenericTrampolineAccount(
  trampolineToken: string,
  remoteUrl: string
) {
  const parsedUrl = new URL(remoteUrl)
  const endpoint = urlWithoutCredentials(remoteUrl)

  const login =
    parsedUrl.username === ''
      ? getGenericUsername(endpoint)
      : parsedUrl.username

  if (!login) {
    return undefined
  }

  const token = await memoizedGetGenericPassword(
    trampolineToken,
    endpoint,
    login
  )

  if (!token) {
    // We have a username but no password, that warrants a warning
    log.warn(`credential: generic password for ${remoteUrl} missing`)
    return undefined
  }

  return { login, endpoint, token }
}
```
