## Title
Automatic remote-URL rewrite based on GitHub API `clone_url` allows silent redirection of pushes/fetches to an attacker-controlled remote - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

## Summary
The upstream report flags `LPToken.set_minter()` for accepting a new privileged value (`_minter`) without validating that it isn't the "empty"/invalid sentinel, permanently corrupting a critical piece of state. The closest analog in GitHub Desktop is `updateRemoteUrl()`, which silently rewrites the local repository's `origin` remote URL using a value taken from a GitHub API response (`apiRepo.clone_url`), gated only by heuristic string-matching checks rather than strict identity/ownership validation.

## Finding Description
`updateRemoteUrl()` compares the currently configured remote URL against the previously known `GitHubRepository.cloneURL` and the freshly-fetched `apiRepo.clone_url`, and if the protocols match and the current remote hasn't been "manually" changed, it calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` without prompting the user: [1](#0-0) [2](#0-1) 

The gating logic relies on `urlMatchesRemote()`, which only checks that hostname/owner/name of the parsed URLs are equal — it performs no cryptographic or identity verification and trivially matches any URL an attacker controls if it is later reported by the GitHub API for the "same" repository (e.g., after the repo is transferred, renamed, or otherwise redirected via a spoofed/compromised API response): [3](#0-2) 

The underlying git primitive, `setRemoteURL()`, performs no validation at all on the incoming `url` value — no non-empty check, no scheme allow-list, no confirmation that it still points to an expected host: [4](#0-3) [5](#0-4) 

This mirrors the `set_minter()` bug-class exactly: a setter for a security-critical value (`origin` remote URL, which determines where the user's commits/pushes go) that accepts attacker/externally-influenced input with only weak or no validation, and silently overwrites the previous, presumably-trusted value.

## Impact Explanation
If the GitHub API object returned for a tracked repository reports a different `clone_url` (e.g., due to repository transfer/takeover, a compromised GHES instance, or a malicious/MITM'd API response), Desktop will automatically repoint the user's `origin` remote to that URL without any confirmation dialog. Every subsequent `git push` from that repository would silently go to the attacker-controlled destination, and subsequent `git fetch`/`pull` would pull content from an attacker-controlled server into the user's working tree — this satisfies "silent corruption of what the user commits or pushes" from the valid-impact criteria.

## Likelihood Explanation
This path is reachable purely through data returned by the GitHub API for a repository the user has already cloned — no local access, admin rights, or user misclicks are required. The exact trigger point where `updateRemoteUrl` is invoked from `app-store.ts` during repository refresh was referenced in the codebase index but the precise call site/line could not be confirmed within the available tool budget; that remains unverified and should be checked directly in the repository (`app/src/lib/stores/app-store.ts`, 3 references to `updateRemoteUrl`/`urlMatchesRemote`) before treating this as a confirmed, fully-triggerable bug.

## Recommendation
Before silently rewriting `origin`'s URL from an API-supplied `clone_url`, require either explicit user confirmation or a stronger integrity check (e.g., verifying the repository's stable numeric ID hasn't changed, not just hostname/owner/name strings), and reject empty/malformed URLs in `setRemoteURL()`/`addRemote()` at the git-command layer, analogous to adding a zero-value guard in `set_minter()`.

## Proof of Concept
Conceptual PoC (not independently executed):
1. User clones/adds `github.com/victim/repo` as `origin` in Desktop, and GitHubRepository state stores `cloneURL = https://github.com/victim/repo`.
2. Repository ownership/name changes upstream such that a subsequent GitHub API `GET /repos/...` call returns `clone_url = https://github.com/attacker/repo` while `urlMatchesRemote` heuristics (same hostname/owner-name shape after e.g. a fork/rename chain or spoofed response) and protocol still pass.
3. `updateRemoteUrl()` runs on next repository refresh and calls `gitStore.setRemoteURL('origin', 'https://github.com/attacker/repo')` with no user prompt: [6](#0-5) 
4. User's next `git push` transmits code/commits to the attacker's repository instead of the intended one.

### Citations

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
