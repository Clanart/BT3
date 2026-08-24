### Title
Automatic remote-URL rewrite trusts unauthenticated `clone_url` field from the GitHub API, allowing silent redirection of push/fetch destination - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
This is a Desktop analog of the OriginToken/Marketplace bug: a stored reference (the git `origin` remote URL) is auto-updated from an external, mutable source of truth (a repository's `clone_url` as returned by the GitHub/GHES API) without validating that the new value still points to the same trusted host. Just as the Marketplace kept trusting a stale token address without validating the migration target, Desktop's `updateRemoteUrl` trusts a server-supplied `clone_url` and rewrites the local `origin` remote without confirming the new host is the one the user originally trusted.

### Finding Description
`repositoryWithRefreshedGitHubRepository` is invoked during normal repository refresh flow in `app-store.ts` and calls `api.fetchRepository(owner, name)` against the account's configured endpoint, then passes the result into `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` decides whether to rewrite the local remote based only on: (1) the remote's protocol scheme matching the new `clone_url`'s scheme, and (2) that the current remote still matches the previously cached `gitHubRepository.cloneURL` (i.e., the user hasn't manually customized it): [2](#0-1) 

Critically, there is no check that the new `clone_url`'s **hostname** matches the account's endpoint hostname or the previous remote's hostname — `protocolsMatch` only compares `http:`/`https:`/etc., not host. `urlMatchesRemote`, used to compute `remoteUrlUnchanged`, likewise only gates on the *old* cached value equaling the *current* remote, not on the *new* value's host: [3](#0-2) 

If satisfied, `gitStore.setRemoteURL` runs `git remote set-url <name> <url>` directly with the server-supplied value, with no host allow-listing: [4](#0-3) [5](#0-4) 

This means any GitHub/GHES API response for the repository object (compromised/rogue GHES instance, or a MITM'd/GHES proxy response for `/repos/:owner/:name`) that returns a `clone_url` pointing at an attacker-controlled host will be silently written into the user's `origin` remote on the very next repository refresh — no prompt, no diff shown to the user, no host-continuity check.

### Impact Explanation
Because the origin remote is rewritten silently, subsequent `git fetch`/`git pull`/`git push` operations transparently target the attacker's host instead of the user's actual GitHub/GHES instance. This satisfies "silent corruption of what the user commits or pushes" and "a git remote/proxy response" attacker control from the scope: an attacker who controls the GHES/API response for a single `fetchRepository` call can hijack all future git network operations for that repository without any user action beyond a normal repository refresh (which happens automatically/periodically). Depending on the credential helper configuration (trampoline credential helper keyed by host), this could also risk sending git credentials/tokens to the attacker's host during the redirected push/fetch.

### Likelihood Explanation
Requires the attacker to control (or MITM) an API response the app already trusts for a signed-in account/endpoint — e.g., a compromised or malicious GitHub Enterprise Server, or a proxy/gateway sitting in front of it that the client is configured to use. This does not require local access, malware, or leaked credentials — it only requires attacker control of the network/API response path for a repository the user has previously cloned/matched to an account, matching the report's "git remote/proxy response" primitive. The refresh path runs during ordinary app usage, so the write happens automatically once the poisoned response is served.

### Recommendation
In `updateRemoteUrl`, before calling `gitStore.setRemoteURL`, validate that the new `clone_url`'s hostname matches either the account's endpoint hostname or the existing remote's hostname, rejecting/ignoring updates that would silently redirect the remote to a different host. Surface any legitimate host change (e.g., real GHES migration) as an explicit, user-confirmed action rather than an automatic background rewrite.

### Proof of Concept
1. Sign in to a GitHub Enterprise Server account in Desktop and have a repository whose `origin` remote matches `gitHubRepository.cloneURL` exactly (default state after clone).
2. Attacker compromises/MITMs the GHES `/repos/:owner/:name` endpoint (or the API proxy in front of it) and returns a JSON body with the same `owner`/`name` but `clone_url: "https://attacker.example.com/owner/name.git"` (same protocol scheme as before, so `protocolsMatch` passes).
3. On the next periodic refresh, `repositoryWithRefreshedGitHubRepository` → `api.fetchRepository` receives the poisoned `clone_url`, and since `urlsMatch` is false while `remoteUrlUnchanged` and `protocolsMatch` are true, `updateRemoteUrl` calls `gitStore.setRemoteURL(name, 'https://attacker.example.com/owner/name.git')`, rewriting `origin` via `git remote set-url` with no user prompt.
4. The user's next `git push`/`fetch` silently targets `attacker.example.com`, exfiltrating code and potentially credentials handled by the host-keyed credential helper. [6](#0-5) [7](#0-6)

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
