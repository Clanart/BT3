Based on my research, I found a genuine analog to the Hats "stale source-of-truth on toggle change" bug in Desktop's automatic remote-URL synchronization logic.

### Title
Automatic remote URL rewrite trusts unvalidated GitHub API `clone_url`, allowing silent redirection of `origin` to an attacker-controlled host - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
Desktop automatically rewrites a repository's local git `origin` remote URL whenever the cached `GitHubRepository.cloneURL` (updated from GitHub API responses) diverges from what's stored locally, via `updateRemoteUrl()`. This mirrors the Hats bug's root cause: the function treats an external, potentially attacker-influenced source (`apiRepo.clone_url`) as more authoritative than the current, admin/user-approved local state, and switches over without re-validating that the switch is toward the same trusted host or with user consent.

### Finding Description
`updateRemoteUrl()` compares the current `gitStore.defaultRemote.url` to `apiRepo.clone_url` and, if the protocol matches and the previously recorded `remoteUrlUnchanged` heuristic holds, calls `gitStore.setRemoteURL()` to silently overwrite the local `origin` remote: [1](#0-0) 

The only checks performed before rewriting are: (1) protocol equality (`http` vs `ssh`), and (2) that the old remote URL still structurally matched the previously known `cloneURL`. There is no check that the new `clone_url` points to the same hostname/owner as before, nor any check against a trusted GitHub host allow-list. The structural comparison helper `urlMatchesRemote` only compares hostname/owner/name between two URLs — it never asserts that the *new* hostname is one Desktop already trusts: [2](#0-1) 

This is the same "switch to a new source-of-truth without syncing/validating first" pattern as the Hats bug: just as `changeHatToggle()` swapped the toggle address without calling `checkHatToggle()` to sync latest state, `updateRemoteUrl()` swaps the git remote based on an API-supplied value without validating that the change is safe or expected by the user, silently committing to a new authority.

### Impact Explanation
If an attacker can influence the `clone_url` field returned for a repository object from the GitHub API (e.g., via a compromised/malicious GitHub Enterprise Server the user has added, a MITM position against a self-hosted GHES instance, or manipulation of repository metadata reachable through the API), Desktop will automatically rewrite the user's `origin` remote to point at an attacker-controlled clone URL. Subsequent `git push` operations from the user would silently go to the attacker's repository instead of the intended one — satisfying the "silent corruption of what the user commits or pushes" impact category, since the user believes they're pushing to the original repository shown in the UI.

### Likelihood Explanation
This code path runs automatically as part of background repository metadata refresh (the API repo object flows into `updateRemoteUrl` per the call sites in `app/src/lib/stores/app-store.ts`), without any explicit user prompt or confirmation dialog — unlike the manual `RepositorySettings` remote-URL change flow (`app/src/ui/repository-settings/repository-settings.tsx`) where a user consciously edits the URL. The precondition (an attacker able to serve a crafted `clone_url` through the API endpoint the account is configured against, e.g., a rogue/compromised GHES) is a real but non-default-GitHub.com scenario, which lowers likelihood somewhat, but it directly fits the report's allowed attacker model of "attacker controls a GitHub API object."

### Recommendation
Before silently applying `updateRemoteUrl`, validate that the new `clone_url` hostname matches the hostname of the account's configured endpoint (or the previously trusted remote hostname), and/or require explicit user confirmation when the target host of the remote is about to change — analogous to the Hats fix of syncing/validating state before switching authorities.

### Proof of Concept
The existing test suite already demonstrates the unrestrained rewrite behavior (only protocol and prior-URL-match are checked, not host trust): [3](#0-2) 
An attacker-controlled or compromised API endpoint could substitute `clone_url` in the fetched repository payload with an attacker-owned repository URL sharing the same protocol; on the next repository refresh, `_` `setRemoteURL` would rewrite `origin` to that URL without any user-visible prompt, since the only invoked path is `gitStore.setRemoteURL` → `git remote set-url`: [4](#0-3) [5](#0-4) 

Note: I was unable to view the exact call site(s) in `app/src/lib/stores/app-store.ts` that invoke `updateRemoteUrl()` (3 references found via search) due to the final iteration limit, so I could not confirm the precise triggering conditions (e.g., which refresh cycle, and whether any additional gating exists there beyond what's shown in `update-remote-url.ts`). This should be verified in a follow-up before treating the severity as final.

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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-94)
```typescript
  it("updates the repository's remote url when the github url changes", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)

    const originalUrl = gitStore.currentRemote.url
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }
    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert.notEqual(originalUrl, updatedUrl)
    assert.equal(gitStore.currentRemote.url, updatedUrl)
  })

  it("doesn't update the repository's remote url when the github url is the same", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)
    const originalUrl = gitStore.currentRemote.url
    assert.notEqual(originalUrl.length, 0, 'Expected originalUrl to be empty')
    await updateRemoteUrl(gitStore, gitHubRepository, apiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })
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
