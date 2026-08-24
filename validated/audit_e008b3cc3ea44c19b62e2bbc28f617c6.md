### Title
Silent, unconfirmed rewrite of the `origin` remote URL from attacker-controlled GitHub API data - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` (invoked from `repositoryWithRefreshedGitHubRepository` during routine background repository refresh) automatically rewrites the local `origin` remote's URL to whatever `clone_url` value the GitHub/GHES API returns for the matched repository, with no user prompt and no validation that the new URL still points at the same host. This mirrors the audit report's bug class of a function that looks like a passive "refresh"/"get" operation but performs an unnoticed, security-relevant state mutation.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` fetches fresh repository metadata via `api.fetchRepository(owner, name)` and, if the repository already has an associated `GitHubRepository`, calls: [1](#0-0) 

into `updateRemoteUrl`: [2](#0-1) 

The function's only safety checks are:
1. `protocolsMatch` — compares URL *scheme* only (`https:` vs `https:`), never the hostname. [3](#0-2) 
2. `remoteUrlUnchanged` — confirms the *current* remote still matches the *previously cached* `gitHubRepository.cloneURL` (i.e., the user hasn't manually retargeted the remote). [4](#0-3) 

If both hold and the newly-fetched `clone_url` differs, Desktop calls `gitStore.setRemoteURL(...)`, which executes `git remote set-url` without any dialog or confirmation: [5](#0-4) [6](#0-5) 

None of the guard conditions validate that the new `clone_url` still points at the same host/owner as before — `urlMatchesRemote` is only used to detect whether the URL is *different*, not to bound what a "safe" new URL looks like: [7](#0-6) 

This means the trust boundary is entirely delegated to the API response body. For GitHub.com this is constrained by GitHub's own repo-rename/transfer semantics, but for a self-hosted / Enterprise Server endpoint (a git server whose response the app trusts as an "API object"), `clone_url` is fully attacker-controlled server-side content. A compromised or malicious GHES instance (or a MITM on that connection) can simply return an arbitrary `clone_url` value and Desktop will silently repoint the user's `origin` remote to it the next time the repository refresh cycle runs — no confirmation dialog, no diff shown to the user.

### Impact Explanation
This corrupts the destination of the user's future `git push` operations without their knowledge: the "get and refresh info" code path (`repositoryWithRefreshedGitHubRepository`, `getRemotes`, etc. — all named/read like passive getters) has the hidden side effect of mutating persistent git configuration (`.git/config`'s remote URL). This falls squarely under "silent corruption of what the user commits or pushes," one of the explicitly valid impact categories, since subsequent pushes would silently go to an attacker-designated destination.

### Likelihood Explanation
Requires the attacker to control (compromise or operate) the GitHub Enterprise/API endpoint that Desktop is configured against for that repository — a plausible "attacker controls ... a git remote/proxy response" scenario per the task's valid-impact definition, and requires no local access, no leaked credentials, and no unusual user action beyond normal periodic repository refresh that Desktop performs automatically.

### Recommendation
- Rename `updateRemoteUrl`/`repositoryWithRefreshedGitHubRepository` to make the mutating side effect explicit (consistent with the original report's recommendation of `getAndUpdate`-style naming), and audit all call sites for unintended implicit remote mutation.
- Before silently rewriting the remote URL, validate that the new URL's hostname matches the previously trusted hostname/endpoint (not just protocol), and/or surface a confirmation prompt to the user when the remote's host is about to change.
- Consider treating a host change in `clone_url` as a higher-risk event requiring explicit user consent, similar to how Desktop already prompts for other trust-sensitive operations.

### Proof of Concept
1. Add a repository in Desktop pointed at a GitHub Enterprise Server endpoint the attacker controls (or can MITM), e.g. `origin = https://ghe.example.com/acme/widgets.git`.
2. Let Desktop associate it with the matched `GitHubRepository` (`gitHubRepository.cloneURL == https://ghe.example.com/acme/widgets.git`), matching current `origin`.
3. Attacker-controlled GHE server responds to the next `GET /repos/acme/widgets` call (triggered by Desktop's periodic `repositoryWithRefreshedGitHubRepository`/background refresh) with `clone_url: "https://ghe.example.com/attacker/widgets-fork.git"` (same scheme/protocol, different owner/repo).
4. `updateRemoteUrl` sees `protocolsMatch === true`, `remoteUrlUnchanged === true` (user hasn't manually changed the remote), and `urlsMatch === false` → it calls `gitStore.setRemoteURL('origin', 'https://ghe.example.com/attacker/widgets-fork.git')` with no dialog.
5. The user's next `git push` (via Desktop's UI, believing they're pushing to `acme/widgets`) silently pushes to the attacker's repository instead.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-20)
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
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L22-34)
```typescript
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
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L36-44)
```typescript
  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
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
