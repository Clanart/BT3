## Analysis

The reported bug class is: **a security-relevant configuration variable can be silently rewritten mid-flight by an untrusted source, with no user confirmation, corrupting subsequent operations that depend on it.**

In `Revolver.sol` the broken invariant was that `commissionPercent`/`baseStake` could change mid-round without user awareness. The Desktop analog is the git **remote URL** — the value that determines where every subsequent `push`/`fetch`/`pull` goes — being silently rewritten by Desktop itself based on an untrusted field (`clone_url`) returned by a GitHub API response, without any user prompt, diff, or confirmation dialog.

### Root cause

`updateRemoteUrl` unconditionally calls `git remote set-url` whenever the API's `clone_url` differs from the local remote, gated only by a loose protocol-scheme check (not a hostname check): [1](#0-0) 

This is invoked from `repositoryWithRefreshedGitHubRepository`, which is exercised on essentially every routine network action (`_push`, `_pull`, `_fetch`, `_addRepositories`, selecting a repository, etc.) via `withRefreshedGitHubRepository`: [2](#0-1) [3](#0-2) 

The guard `protocolsMatch` only compares the URL *scheme* (`https` vs `https`), never the hostname: [4](#0-3) 

`remoteUrlUnchanged` merely checks that the *current* local remote still matches the *previously cached* GitHub `cloneURL` — true for the overwhelming majority of normal repositories — so it does not protect against a spoofed API response: [5](#0-4) 

`setRemoteURL` then executes `git remote set-url` with zero user interaction: [6](#0-5) [7](#0-6) 

### Title
Silent, unconfirmed rewrite of `origin` remote URL from an untrusted GitHub API field enables push/fetch redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
Desktop automatically executes `git remote set-url` whenever the `clone_url` field returned by `GET repos/{owner}/{name}` differs from the locally configured remote, gated only by a same-protocol-scheme check that does not verify the hostname/owner/name. This call path runs on nearly every background/foreground network action, with no user prompt or diff shown.

### Finding Description
`repositoryWithRefreshedGitHubRepository` fetches repository metadata from the GitHub API on almost every push, pull, fetch, and repository-add/select operation, then hands the result to `updateRemoteUrl`. That function compares the new `clone_url` to the local remote via `urlMatchesRemote` (hostname+owner+name), but the only safety check preventing an update is that the *protocol scheme* matches — hostname is never validated. If an attacker can influence this single API JSON field (e.g., via a compromised/malicious proxy or GHES instance sitting between Desktop and the API endpoint — an accepted attacker primitive per this report's scope, "a git remote/proxy response"), Desktop will silently rewrite the user's `origin` remote to an attacker-controlled URL with matching scheme but a different host/owner/repo, with no popup, confirmation, or diff review of the change.

### Impact Explanation
Once the remote is rewritten, all subsequent user-initiated `git push` operations silently go to the attacker's endpoint via `push()`/`pushRepo`, using credentials resolved for that (attacker) remote through `envForRemoteOperation`. This is a direct instance of "silent corruption of what the user commits or pushes," and can also result in credential/token exfiltration since Desktop will authenticate against whatever host is now configured as `origin`.

### Likelihood Explanation
The refresh path is triggered continuously during normal use (every push/pull/fetch and repository selection), so exploitation does not require any unusual user action beyond ordinary Desktop usage while the attacker sits in the network/API path (e.g., a rogue or compromised GitHub Enterprise Server/proxy). No local access, admin rights, or pre-existing host malware is required — only control of the API response content in transit, which is within this report's accepted attacker model.

### Recommendation
- Do not silently rewrite the git remote based on API data; require explicit user confirmation before changing `origin`'s URL (similar to how `RepositorySettings` requires the user to type/submit the URL themselves).
- If automatic redirect-following is desired (e.g., for legitimate repository renames), validate that the new URL still points to the same GitHub host used for authentication and require it to be a known redirect target reported directly by a trusted, already-authenticated request, not an unauthenticated diff of two loosely-parsed URLs.
- At minimum, compare full origin (scheme + host) rather than only the scheme in `protocolsMatch`.

### Proof of Concept
1. Victim has a repository open in Desktop with `origin` matching a GitHub-hosted `clone_url` (normal state).
2. Attacker controls or compromises the network path/proxy in front of the API endpoint Desktop talks to (GitHub.com or a GHES instance) and, on the next `GET repos/{owner}/{name}` call, returns a JSON body identical except `clone_url` set to `https://attacker.example/owner/name.git`.
3. On the victim's next push/pull/fetch, `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` runs: `protocolsMatch` is true (https == https), `remoteUrlUnchanged` is true, `urlsMatch` is false → `gitStore.setRemoteURL('origin', 'https://attacker.example/owner/name.git')` executes silently.
4. The victim's subsequent `git push` (initiated normally through Desktop's UI) now sends commits/credentials to `attacker.example` with no warning ever shown.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L4890-4907)
```typescript
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
