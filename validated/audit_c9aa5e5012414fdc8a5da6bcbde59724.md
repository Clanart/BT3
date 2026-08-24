Based on my research, the strongest legitimate analog is a silent, unconfirmed git-remote rewrite driven entirely by attacker-controlled GitHub API data — a broken invariant of the same shape as the Sherlock finding (an automated, trust-based side effect executed without adequate validation of the value it consumes).

### Title
Silent, unconfirmed rewrite of the git `origin` remote URL to an attacker-controlled host driven by an unvalidated GitHub API `clone_url` - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
Whenever Desktop refreshes a repository's associated GitHub metadata, it calls `updateRemoteUrl()`, which will automatically run `git remote set-url` against the local repository using the `clone_url` field taken directly from the GitHub API response, with no user confirmation and no restriction on the new URL's host, path, or owner.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` fetches the repository from the API and unconditionally forwards the result to `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl()` decides whether to rewrite the remote based on three checks: whether the new `clone_url`'s protocol matches the current remote's protocol, whether the *current* remote still matches the *previously stored* `cloneURL`, and whether the *new* `clone_url` differs from the current remote: [2](#0-1) 

Critically, none of these checks constrain the **new** `clone_url` to the same host/owner/name as the repository Desktop believes it's tracking — `urlMatchesRemote` is only used to compare the *old* value against the current remote, not to validate the *new* value being written: [3](#0-2) 

If those conditions are satisfied, Desktop calls `gitStore.setRemoteURL`, which shells out to `git remote set-url <name> <url>` with whatever string the API returned as `clone_url`: [4](#0-3) [5](#0-4) 

This mirrors the root cause pattern in the Sherlock report: an automated side effect (there, `SetWithdrawAddress`; here, `set-url`) is executed based on trust in an external/attacker-influenceable value (there, a chain parameter; here, a GitHub API field), without adequately validating the invariant the code assumes holds (there, `WithdrawAddrEnabled`; here, "the new clone_url still refers to the same repo/host the user is working with").

### Impact Explanation
Since GitHub Desktop supports GitHub Enterprise Server accounts with attacker/administrator-controlled endpoints, and the API response is fetched over a connection whose CA trust can be operator/organization controlled in GHES environments, a malicious or compromised API endpoint (a "GitHub API object" per the allowed threat model) can return an arbitrary `https://` `clone_url`. Desktop will then silently retarget `origin` to that URL with **no dialog, warning, or diff shown to the user**. All subsequent pushes performed through Desktop's UI (which trusts `gitStore.defaultRemote`/`branch.upstreamRemoteName`, see `push.ts`) will silently send the user's commits — and, depending on credential-helper matching, authentication material — to the attacker-controlled host instead of the intended repository. This is exactly the kind of "silent corruption of what the user pushes" / credential-exfiltration risk called out as valid impact.

### Likelihood Explanation
This requires the account's endpoint (typically only realistic for self-hosted GHES setups, or a MITM'd/compromised API/proxy response for the tracked repository) to be attacker-influenced, and requires that the current local remote still matches the previously cached `cloneURL` (i.e., the user hasn't manually customized `origin`) — a common state for most repositories. No user interaction beyond Desktop's normal periodic repository refresh is required, which happens automatically in the background.

### Recommendation
Before calling `setRemoteURL`, validate that the **new** `clone_url`'s hostname matches the account's configured endpoint hostname (or at minimum the current remote's hostname), not just that the protocol matches; and prompt/confirm with the user before silently rewriting a remote URL to a different host, the same way Desktop already prompts for other trust-sensitive changes.

### Proof of Concept
1. Add a repository whose GitHub account endpoint is a GitHub Enterprise Server instance (or intercept/compromise the API response for `fetchRepository`).
2. Ensure the local `origin` remote currently matches the stored `gitHubRepository.cloneURL` (default state after cloning).
3. Have the API return an `IAPIRepository` with the same `https` protocol but `clone_url: "https://evil.example.com/attacker/repo.git"`.
4. Trigger a background repository refresh (`repositoryWithRefreshedGitHubRepository`, invoked periodically/on repo focus).
5. Observe `git remote -v` for the repository now shows `origin` pointing at `https://evil.example.com/attacker/repo.git` with no prompt shown to the user; subsequent pushes via Desktop's Push button go to the attacker host.

### Citations

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
