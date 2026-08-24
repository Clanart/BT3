### Title
Silent, unattended repository remote-URL rewrite driven by GitHub API `clone_url` field enables remote hijack — ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` compares the locally configured `origin` remote against the `clone_url` field returned by the GitHub API for the associated `GitHubRepository`, and — if a small set of heuristic conditions hold — silently rewrites the user's local git remote URL to whatever the API returned, without ever validating that the new host is the same as before.

### Finding Description
`updateRemoteUrl` reads `apiRepo.clone_url` from an API response and decides whether to call `gitStore.setRemoteURL(...)` to change the user's local `origin` remote: [1](#0-0) [2](#0-1) 

The only checks performed before rewriting the remote are:
1. `protocolsMatch` — only compares the URL *scheme* (`https` vs `https`), not the host/owner/name. [3](#0-2) 
2. `remoteUrlUnchanged` — checks that the *previously cached* `gitHubRepository.cloneURL` still matches the current local remote, using `urlMatchesRemote`, which compares hostname/owner/name. [4](#0-3) [5](#0-4) 
3. `!urlsMatch` — i.e., the *new* `clone_url` differs from what's already configured.

None of these three checks require the new `clone_url` to point at the same host, owner, or repository name as before. As long as the previously-cached `cloneURL` still matches the current remote (the normal, unmodified case) and the scheme is unchanged, the function will happily rewrite `origin` to point to a completely different host if the API's `clone_url` value says so.

The broken invariant is: **the trust boundary is the GitHub API response itself, with no corroborating check that the new remote is even related to the same repository.** This is the direct analog of the reported bug class — a value derived from an external, attacker-influenceable source (`totalBorrowed`/utilization in the original report; `clone_url` in this API response here) is fed directly into a state-mutating action (`emissionRate` update there; local git remote rewrite here) without a sanity/consistency check against historical or trusted values beyond a single shallow comparison.

`clone_url` for a `GitHubRepository` is sourced from GitHub Enterprise (self-hosted) or any endpoint configured by the user (`api.github.com`, GHES, or via an intercepting proxy) — this falls squarely within the allowed "git remote/proxy response" attacker model. A GHES admin, a compromised/rogue enterprise API endpoint, or a MITM proxy sitting in front of the configured endpoint can serve an API response for the repository object with an arbitrary `clone_url`, and Desktop will use it to overwrite the user's `origin` remote the next time repository metadata is refreshed.

### Impact Explanation
Silent corruption of what the user pushes: since Desktop uses `origin`'s URL for subsequent `git push`/`git fetch` operations, once the remote is rewritten, all future pushes/fetches transparently go to the attacker-controlled endpoint instead of the real repository. This can lead to:
- Exfiltration of proprietary source code pushed by the victim.
- Credential exfiltration via the OS credential helper / stored HTTPS credentials being sent to the attacker's host on the next authenticated push.
- The user unknowingly committing to (and believing they pushed to) an attacker's fork/mirror while their real upstream repository silently diverges — a stealthy supply-chain risk.

This matches the requested impact class of "silent corruption of what the user commits or pushes" / "credential ... exfiltration" originating from "a git remote/proxy response."

### Likelihood Explanation
The rewrite is not gated behind any explicit user confirmation, diff/prompt, or hostname allowlist — it runs automatically as part of routine background repository-metadata refresh flows that call `updateRemoteUrl` whenever the app refreshes the associated `GitHubRepository`/API data. Any actor who controls (or can intercept/tamper with) responses from the endpoint the user has configured (GHES instance, or any TLS-terminating proxy in an enterprise network) can trigger it without any local access, credentials, or unusual user action — the user just needs Desktop open with normal auto-refresh happening.

### Recommendation
1. Do not trust `clone_url` as sufficient justification to auto-rewrite a remote. At minimum, require that the new URL's hostname/owner/name match the *account's configured endpoint* and the repository's already-known owner/name (i.e., only allow scheme/subdomain-level normalization, not arbitrary host changes).
2. Require explicit user confirmation (a prompt) before silently changing `origin`'s URL, especially when the host component differs.
3. Treat `clone_url` (and other API-supplied repository identity fields) as data to display/compare, not as an unconditional trigger for local git state mutation.
4. Add logging/telemetry and a diff-preview so users can see exactly what changed before it's applied.

### Proof of Concept
1. Victim adds a repository backed by a self-hosted GitHub Enterprise endpoint (or any endpoint reachable through a proxy the attacker controls), with `origin` set to `https://ghe.company.com/org/repo.git`, matching the cached `gitHubRepository.cloneURL`.
2. Attacker, controlling the GHE instance/API proxy, alters the JSON response for `GET /repos/org/repo` so that `clone_url` becomes `https://attacker.example.com/org/repo.git` (same scheme, different host).
3. On the next periodic repository/API refresh, Desktop invokes `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)`:
   - `protocolsMatch` → true (both `https`).
   - `remoteUrlUnchanged` → true (cached `cloneURL` still equals the current, legitimate remote).
   - `urlsMatch` → false (attacker URL differs).
4. All three conditions are satisfied, so `gitStore.setRemoteURL('origin', 'https://attacker.example.com/org/repo.git')` executes silently. [6](#0-5) 
5. The victim's next `git push`/`git fetch` in Desktop transparently targets the attacker's server, and any stored HTTPS credentials for that operation are sent to it. [7](#0-6)

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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L29-44)
```typescript
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
