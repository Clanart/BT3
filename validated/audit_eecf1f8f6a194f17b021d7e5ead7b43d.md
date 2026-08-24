## #Vulnerability found for this question

### Title
Unvalidated GitHub API `clone_url` silently rewrites the local git remote to an attacker-controlled host - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` is invoked automatically whenever Desktop refreshes a repository's associated GitHub metadata (`repositoryWithRefreshedGitHubRepository` in `app-store.ts`), and it silently rewrites the local `origin` remote to whatever `clone_url` the API call `api.fetchRepository(owner, name)` returns, with no validation that the new URL's hostname belongs to the same GitHub endpoint/account the user originally configured.

### Finding Description
`repositoryWithRefreshedGitHubRepository` fetches fresh repository data from the GitHub API and, if the repository already has an associated `gitHubRepository`, unconditionally calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [1](#0-0) 

`updateRemoteUrl` then decides whether to change the actual git remote based only on three checks: (1) the remote's protocol matches the new URL's protocol, (2) the current remote URL still equals the previously stored `gitHubRepository.cloneURL` (i.e., the user hasn't manually edited it), and (3) the new URL differs from the current one. If all three hold, it calls `gitStore.setRemoteURL(...)` with the API-supplied `clone_url` verbatim: [2](#0-1) 

Crucially, none of these checks constrain the **hostname/owner/name** of the new URL to match the account's expected endpoint or the previously known owner/repo — `protocolsMatch` only compares the URL scheme (`https:` vs `https:`), not the host. `urlMatchesRemote`, used elsewhere for detecting matches, does compare hostname/owner/name, but it is only used here to compute `urlsMatch` (used as a negation gate to decide *whether* to update), not as a safety check on the new value being written.

`setRemoteURL` then directly executes `git remote set-url <name> <url>` with that value: [3](#0-2) 

The `clone_url` field comes straight from the parsed JSON response of the `GET repos/{owner}/{name}` API call: [4](#0-3) 

Nothing in this path re-validates that `clone_url`'s host is the same GitHub/GHES endpoint used to make the request. This mirrors the report's core issue — a single trusted data source (the Chainlink oracle price / here the GitHub API repository object) is consumed without any secondary validation or "reserve" cross-check, so if that single source returns unexpected or attacker-influenced data, the invariant ("the remote we push to is the repo the user added") silently breaks instead of being safely rejected.

### Impact Explanation
If an attacker can influence the JSON returned for a `repos/{owner}/{name}` request — e.g., a compromised or malicious GitHub Enterprise Server instance, an on-path/proxy attacker able to tamper with the API response for a configured enterprise endpoint, or any scenario where the API object served to Desktop is attacker-controlled — Desktop will silently repoint the user's `origin` remote to an arbitrary host chosen by the attacker, as long as the protocol scheme matches and the user hasn't manually customized the remote. Subsequent `git push`/`git fetch` operations initiated by the user through the normal UI will silently go to the attacker's server instead of the intended repository, satisfying the "silent corruption of what the user commits or pushes" impact category. Depending on credential-helper matching behavior this can also risk exposing push credentials to the attacker's host.

### Likelihood Explanation
This code path runs automatically as part of routine background refreshes of GitHub repository metadata (`repositoryWithRefreshedGitHubRepository`), not behind any extra user confirmation, so a single malicious/compromised API response is sufficient to trigger it — no local access, admin rights, or social engineering is required, only control over (or tampering with) the API response for that one endpoint.

### Recommendation
Before calling `gitStore.setRemoteURL`, additionally validate that the new `clone_url`'s hostname matches the hostname of the account's endpoint (and ideally warn/prompt the user) instead of relying solely on protocol-match plus "unchanged since last fetch" heuristics. Consider surfacing an explicit confirmation UI when a tracked repository's remote is about to change host, rather than applying it silently.

### Proof of Concept
1. User adds a GitHub Enterprise Server repository to Desktop; `gitHubRepository.cloneURL` and the git `origin` remote both point to `https://ghes.company.com/org/repo.git`.
2. Attacker compromises the GHES instance (or intercepts the API response for `GET repos/org/repo`) and returns a repository object with `clone_url: "https://attacker.evil/org/repo.git"` (same `https:` protocol).
3. On the next periodic refresh, `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` sees: protocol matches, current remote still equals the previously stored clone URL (untouched by user), and the new URL differs — all three gating conditions pass.
4. Desktop silently executes `git remote set-url origin https://attacker.evil/org/repo.git`.
5. The user, unaware, performs a normal push from the Desktop UI; the push (and any embedded credentials handled by the git credential flow) goes to `attacker.evil` instead of the intended repository.

### Citations

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

**File:** app/src/lib/api.ts (L1010-1030)
```typescript
  public async fetchRepositoryCloneInfo(
    owner: string,
    name: string,
    protocol: GitProtocol | undefined
  ): Promise<IAPIRepositoryCloneInfo | null> {
    const response = await this.ghRequest('GET', `repos/${owner}/${name}`, {
      // Make sure we don't run into cache issues when fetching the repositories,
      // specially after repositories have been renamed.
      reloadCache: true,
    })

    if (response.status === HttpStatusCode.NotFound) {
      return null
    }

    const repo = await parsedResponse<IAPIRepository>(response)
    return {
      url: protocol === 'ssh' ? repo.ssh_url : repo.clone_url,
      defaultBranch: repo.default_branch,
    }
  }
```
