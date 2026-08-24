This confirms `repositoryWithRefreshedGitHubRepository` runs automatically (on repository selection, background account refresh, after publish, etc.) at [1](#0-0)  and calls `updateRemoteUrl` using data straight from `api.fetchRepository(owner, name)` [2](#0-1) , with no verification that the new URL's host matches the account's configured endpoint before silently rewriting the local `origin` remote.

### Title
Silent, unauthenticated rewrite of a repository's Git remote URL from untrusted GitHub/GHES API `clone_url` data - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` automatically rewrites the user's `origin` remote to whatever `clone_url` the configured API endpoint returns for a repository, without ever checking that the new URL still belongs to the same host/endpoint the user authenticated against. This is functionally the same defect class as the Mochi `registerAsset()` bug: a value that should only be set once (or only updated under strict validation) is silently overwritten based on an attacker-influenced input, changing how the surrounding system subsequently behaves (in this case, where the user's future `git fetch`/`git push` traffic goes).

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository`, which runs the associated GitHub API request and, if it succeeds, calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [3](#0-2) 

Inside `updateRemoteUrl`, the only checks performed before calling `gitStore.setRemoteURL(...)` are: (1) that the git and API URL protocols match, and (2) that the *previously recorded* clone URL matched the current remote (i.e. the user hasn't manually customized it) and (3) that the URLs differ: [4](#0-3) 

There is no check that the hostname of the newly supplied `apiRepo.clone_url` matches the hostname of `account.endpoint`/the existing remote's hostname. The `apiRepo` value itself comes directly from `api.fetchRepository(owner, name)`, which is a network call to `account.endpoint` — the exact endpoint the user configured for a GitHub Enterprise (GHES) sign-in, or `api.github.com`: [5](#0-4) 

This flow runs automatically and silently in many contexts that require no interactive user consent: whenever a repository is selected (`_selectRepositoryRefreshTasks`), whenever an account changes, and after publishing a repository: [6](#0-5) 

Because Desktop unconditionally trusts whatever `clone_url` field the configured endpoint returns for the repository object, a malicious or compromised GHES instance, or a network-position attacker acting as a proxy/MITM for that endpoint, can respond with an arbitrary `clone_url` (e.g. pointing to an attacker-controlled server) and Desktop will rewrite the local `origin` remote to that value with no user prompt, no diff shown, and no host-consistency check.

### Impact Explanation
Once `origin` is silently repointed, every subsequent `git fetch`/`git pull`/`git push` the user performs targets the attacker-controlled destination instead of the legitimate repository. This can lead to: pushing the user's commits (and, depending on the credential helper/trampoline configuration, credentials over that connection) to an attacker server, or having subsequent fetches pull attacker-supplied history/objects into the user's working repository — a silent corruption of what the user pushes and fetches, without their awareness, since the UI gives no confirmation dialog for this specific rewrite (`AddSSHHost`-style prompts exist for SSH host-key changes, but there is no analogous prompt for HTTPS remote URL changes driven by API data).

### Likelihood Explanation
Exploitation requires the user to be signed into (or the app to already be tracking) a GitHub Enterprise endpoint or a network path to `api.github.com`/GHES that the attacker can influence at the proxy/MITM layer — both are within the accepted attacker model ("a GitHub API object" and "a git remote/proxy response"). No local access, malware, or leaked credentials are required. The rewrite path is exercised automatically and frequently (every repository selection, background account refresh, and publish flow), maximizing exposure once such a network position or malicious endpoint exists.

### Recommendation
Before calling `gitStore.setRemoteURL`, verify that the hostname component of `apiRepo.clone_url` matches the hostname of the account's `endpoint` (or the existing remote), and require explicit user confirmation (similar to the existing `AddSSHHost` dialog pattern) before silently changing a tracked repository's remote URL to a different host.

### Proof of Concept
1. Sign in to a GitHub Enterprise endpoint, or be positioned to intercept/modify HTTP(S) responses from the configured API endpoint (e.g. malicious/compromised GHES admin, or MITM proxy).
2. Clone/track a repository whose `origin` remote matches the recorded `cloneURL` for that `GitHubRepository`.
3. On the next automatic refresh path (e.g. `_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository`, triggered simply by selecting the repository in Desktop), have the endpoint's repository API response return a `clone_url` pointing at an attacker-controlled host, keeping the same protocol (e.g. `https://attacker.example/foo/bar.git`).
4. `updateRemoteUrl` passes the `protocolsMatch`/`remoteUrlUnchanged`/`!urlsMatch` checks and silently calls `gitStore.setRemoteURL('origin', 'https://attacker.example/foo/bar.git')`, rewriting the user's `origin` without any prompt.
5. The user's next `git push`/`git fetch` now targets the attacker's host.

### Citations

**File:** app/src/lib/stores/app-store.ts (L2250-2257)
```typescript
    this.startBackgroundFetching(repository, !previouslySelectedRepository)
    this.startPullRequestUpdater(repository)

    this.startBackgroundPruner(repository)

    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
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
