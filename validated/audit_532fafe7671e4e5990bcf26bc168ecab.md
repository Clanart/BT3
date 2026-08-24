### Title
GitHub API-supplied `clone_url` silently rewrites the local git remote, redirecting future pushes/fetches - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` mutates the repository's default git remote URL automatically whenever the cached `IAPIRepository` object reports a different `clone_url`, based only on a few heuristic checks. Because the `clone_url` value originates from a GitHub API response (an attacker-influenceable object per the accepted impact category), this is a direct analog to the ERC-20 report's core defect: an "authoritative" but externally-controlled metadata field is allowed to silently overwrite a value the user relies on (there, `decimals`/`name` invalidating permits; here, the remote URL that governs where the user's commits get pushed/fetched).

### Finding Description
`updateRemoteUrl` compares the repository's current default remote URL to `apiRepo.clone_url` and, if a set of conditions hold, calls `gitStore.setRemoteURL(...)` to overwrite the remote without any user prompt: [1](#0-0) 

The three guard conditions are:
1. `protocolsMatch` — only compares the URL scheme (`https:` vs `git@...` ssh-style), not host/owner. [2](#0-1) 
2. `remoteUrlUnchanged` — checks that the *current* remote still matches the *previously cached* `gitHubRepository.cloneURL` via `urlMatchesRemote`. [3](#0-2) 
3. `!urlsMatch` — the new `clone_url` differs from what's on disk. [4](#0-3) 

If all three hold, `gitStore.setRemoteURL` is invoked, which runs `git remote set-url` on disk with no confirmation dialog and no diffing of the actual host/owner/name against a trust anchor: [5](#0-4) [6](#0-5) 

`urlMatchesRemote` (used for the "unchanged" guard) only compares hostname/owner/name structurally; it does not pin to a specific known-good host set, so a `clone_url` returned for the same nominal `owner/name` but pointing at a different host or protocol variant could still satisfy the match logic depending on how the value was cached: [7](#0-6) 

This mirrors the ERC-20 report's broken invariant exactly: a value the app treats as "trusted, non-user-facing metadata" (there: token `decimals`/`name`; here: the git `clone_url` reported by the API) is allowed to overwrite something the user is actively relying on (a signed permit / the destination of their next `git push`) purely because an upstream/owner-controlled source changed it, with no user-visible confirmation step.

### Impact Explanation
If a repository is renamed, transferred, or its GitHub-side metadata is otherwise altered (including scenarios where an attacker who briefly gains control of a repo's naming/ownership on the GitHub side, or a compromised/malicious GHES endpoint, returns a manipulated `clone_url`), Desktop will silently repoint the user's `origin` remote via `git remote set-url` the next time repository info is refreshed. Because Desktop does this without any dialog, the user has no chance to notice the destination has changed — their next `git push` (and displayed "in sync with origin" state derived from `git fetch`) transparently operates against a different remote than the one they originally configured. This is silent corruption of what the user pushes to, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
The change requires the app to fetch/cache an `IAPIRepository` object whose `clone_url` differs from disk, which happens routinely on background repository refreshes. The only real gate is the URL-parsing based match logic (`urlMatchesRemote`/`parseRemote`), not a strict pinned-endpoint check, and the operation happens with zero user interaction — it is a background write triggered purely by data returned from the GitHub API, satisfying the "attacker controls a GitHub API object" impact vector without needing local access, admin rights, or social engineering.

### Recommendation
Do not auto-mutate the on-disk git remote from API-supplied `clone_url` values without explicit user confirmation. At minimum, surface a prompt/notification before calling `gitStore.setRemoteURL`, and validate that the new URL's hostname is an already-trusted endpoint (e.g., the account's configured GitHub.com/GHES endpoint) rather than relying solely on owner/name string matching.

### Proof of Concept
1. Add repository A (`origin` = `https://github.com/owner/repo.git`) to Desktop and let it cache the associated `GitHubRepository.cloneURL`.
2. Cause the next refresh cycle to return an `IAPIRepository` object whose `clone_url` differs (e.g., via a compromised/spoofed API response for a GHES endpoint, or a legitimate rename/ownership change that an attacker can trigger on the GitHub side) while keeping the same protocol scheme.
3. Observe `updateRemoteUrl` run: `protocolsMatch` is true, `remoteUrlUnchanged` is true (matches cached `cloneURL`), `urlsMatch` is false → `gitStore.setRemoteURL` fires, silently rewriting `origin` on disk as shown by the existing unit test that asserts the URL updates with no user interaction: [8](#0-7) .
4. The user's next `git push`/`git fetch` now targets the new URL without ever being shown a confirmation dialog.

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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-81)
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
```
