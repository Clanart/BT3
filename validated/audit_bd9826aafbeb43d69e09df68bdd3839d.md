### Title
Attacker-controlled GitHub API `clone_url` silently rewrites a repository's trusted git remote - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` mutates a repository's `origin` remote URL based solely on the `clone_url` field of an `IAPIRepository`/`IAPIFullRepository` object returned from the GitHub/GHES API, with no verification that the new host is the same as the one the user originally configured. This function is invoked automatically, with no user confirmation, every time Desktop refreshes GitHub metadata — which happens on repository selection, before every push, pull, and fetch.

### Finding Description
`updateRemoteUrl` only checks that the git URL protocol is unchanged and that the *current* remote still matches what was previously cached from the API; it never checks that the *new* `clone_url` host matches the *old* remote's host: [1](#0-0) 

`urlMatchesRemote` compares hostname/owner/name, but it's only used to detect whether the remote changed (`urlsMatch`) and whether the user has customized the remote away from the previously-known API URL (`remoteUrlUnchanged`) — never to constrain what host the *new* URL is allowed to point to: [2](#0-1) 

This function is called from `repositoryWithRefreshedGitHubRepository`, which fetches repository data live from the API using `api.fetchRepository(owner, name)` and unconditionally feeds the response's `clone_url` into `updateRemoteUrl`: [3](#0-2) 

That refresh path (`withRefreshedGitHubRepository` → `repositoryWithRefreshedGitHubRepository`) runs automatically before every push, pull, and fetch when there's no cached account association: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

Once the checks pass, `gitStore.setRemoteURL` runs `git remote set-url` with no additional confirmation from the UI: [8](#0-7) [9](#0-8) 

The broken invariant is the same shape as the original report's `receiveCollateral()`: a function that mutates trusted state (`origin` URL used for every future git network operation) trusts an untrusted/attacker-influenceable input (the API response object) without verifying it originates from — or targets — the expected authority (the original remote host).

### Impact Explanation
An attacker who can influence the `IAPIFullRepository`/`IAPIRepository` object returned to `fetchRepository()` — e.g., a compromised or malicious GitHub Enterprise Server instance the user has added as an account, a network/TLS-terminating proxy trusted by the corporate root CA, or any man-in-the-middle capable of tampering with the JSON API response for that endpoint — can set `clone_url` to an arbitrary URL with the same protocol scheme (any `https://` host). Desktop will then silently execute `git remote set-url origin <attacker-url>` with no dialog or warning. Subsequent user pushes/pulls/fetches transparently target the attacker's git server instead of the legitimate GitHub/GHES origin, resulting in silent corruption of where the user's commits are pushed (potential source-code exfiltration to the attacker) and the origin of code fetched into the user's working tree (supply-chain risk if the attacker serves malicious commits back).

### Likelihood Explanation
Exploitation requires the attacker to control (or MITM) a GitHub Enterprise / GitHub API response the app trusts for an account already configured in Desktop — this is a realistic threat for a compromised or hostile GHES admin, or a corporate/adversarial TLS-intercepting proxy, matching the "attacker controls...a GitHub API object" criterion in scope. No local access, malware, or unusual user action is required beyond having previously added the account and having the repository open; the rewrite occurs automatically during normal push/pull/fetch background refresh.

### Recommendation
In `updateRemoteUrl`, in addition to checking protocol equality, verify that the new `clone_url`'s hostname matches the existing remote's hostname (or is an allow-listed host for the account's endpoint) before calling `gitStore.setRemoteURL`. If the hostname changes, surface a confirmation dialog to the user instead of silently rewriting the remote.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop whose API the attacker controls (compromised GHES instance, or a MITM proxy trusted by the OS/Electron TLS store).
2. Open/clone a repository tracked against that account, with `origin` pointing at `https://ghe.example.com/owner/repo`.
3. Have the controlled API return, for `GET /repos/owner/repo`, a JSON payload with `"clone_url": "https://attacker.example.com/owner/repo"` (same `https` scheme).
4. Trigger any push/pull/fetch (or simply reselect the repository) — `withRefreshedGitHubRepository`/`repositoryWithRefreshedGitHubRepository` fetches this payload and calls `updateRemoteUrl`, which passes the protocol check and the "unchanged from previously cached API URL" check, and since `urlMatchesRemote(new, old)` is false, invokes `gitStore.setRemoteURL('origin', 'https://attacker.example.com/owner/repo')` with no user prompt.
5. Verify via `git remote -v` that `origin` now points to `attacker.example.com`, and that the next push/fetch silently targets the attacker's server.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L5452-5456)
```typescript
  public async _pull(repository: Repository): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPull(repository)
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

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```
