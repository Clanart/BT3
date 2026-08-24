### Title
Silent Remote URL Rewrite From Attacker-Controlled GitHub API `clone_url` Redirects Push/Fetch Traffic - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
GitHub Desktop periodically reconciles a repository's local `origin` remote URL with the `clone_url` reported by the GitHub API for the associated `GitHubRepository`. The reconciliation logic in `updateRemoteUrl` will silently rewrite the local git remote to whatever URL the API returned, as long as the protocol matches and the *previous* remote matched the previously-cached API URL — it does not require the *new* URL to share the same host, owner, or repo name as the existing remote.

### Finding Description
`updateRemoteUrl` compares the current default remote's URL against `apiRepo.clone_url` and, if the protocol is unchanged and the user hasn't manually diverged from the last-known API URL, calls `gitStore.setRemoteURL` to overwrite the remote with the new API-provided URL: [1](#0-0) 

The gating condition is `protocolsMatch && remoteUrlUnchanged && !urlsMatch`. Critically, `!urlsMatch` is satisfied by *any* mismatch between the old and new URL — including a mismatch in hostname. `urlMatchesRemote` only returns `true` when hostname, owner, and name all match; any other kind of difference (including pointing to a completely different host) causes it to return `false`, which is exactly the trigger condition needed for the rewrite to proceed: [2](#0-1) 

This means the code was written to handle a legitimate case (GitHub renamed/transferred the repo, so `clone_url` legitimately changed), but the guard does not verify that the *new* value is still bound to the expected host/owner/name — it only checks that the *old* value hadn't been tampered with by the user. There is no allow-list or hostname pinning check on `updatedRemoteUrl` itself before it's written into the user's `.git/config` via `setRemoteURL`.

Since `apiRepo` originates from a GitHub API response (`IAPIRepository`), any actor able to influence that response for a repository the user has added as a GitHub-backed repository (a compromised/misbehaving GitHub Enterprise Server the user has authenticated against, a malicious redirect/transfer of a repo the user tracks, or a network path capable of tampering with the API response before TLS termination issues are otherwise mitigated) can cause Desktop to silently repoint the trusted `origin` remote to an arbitrary destination — without any user prompt, confirmation dialog, or diff shown.

### Impact Explanation
Once the remote URL is silently rewritten, subsequent `git push` operations performed via Desktop's normal "push" button will transmit the user's commits to the attacker-controlled destination instead of the intended repository, and subsequent `fetch`/`pull` operations will pull attacker-supplied history that the user may believe originates from the original, trusted repository. This falls squarely into "silent corruption of what the user commits or pushes" — the user never explicitly changed their remote, and the UI gives no indication that the destination changed. Depending on the new host and whether it echoes/relays back to the legitimate origin, this could also facilitate credential/token exposure to an unintended host via HTTPS auth flows.

### Likelihood Explanation
Exploitation requires the attacker to control (or corrupt) the `clone_url` field of a GitHub API repository object that Desktop associates with a tracked local repository — this is plausible for GitHub Enterprise Server accounts the user has added (compromised/malicious GHES instance), or scenarios where API responses are tampered with in transit. The check `remoteUrlUnchanged` and `protocolsMatch` are not meaningful security boundaries: they only preserve the "protocol" (https/https) and ensure the user hasn't manually customized their remote, but say nothing about validating the new destination. This is a realistic, low-friction path for a scenario already contemplated by GitHub Desktop's threat model (untrusted/attacker-influenced repository and API objects), matching the "GitHub API object" and "git remote" attacker primitives called out as valid.

### Recommendation
Before calling `gitStore.setRemoteURL` with `updatedRemoteUrl`, validate that the new URL's hostname matches the account's configured endpoint hostname (`getHTMLURL(account.endpoint)`), not just that the protocol is unchanged. Any change spanning a different hostname should require explicit user confirmation via a dialog rather than being applied silently, and ideally should be logged/surfaced in the UI (e.g., a "your remote has changed" banner) so the user can review it before pushing.

### Proof of Concept
1. User has a repository tracked with GitHub Desktop against a GitHub Enterprise Server account (or against a repo whose upstream API metadata can be influenced/tampered with).
2. Desktop refreshes GitHub repository association info and receives an `IAPIRepository` object whose `clone_url` has been changed by the attacker to `https://attacker.example.com/owner/name.git` (same protocol, `https`, as the existing remote).
3. `updateRemoteUrl` runs: `protocolsMatch` is `true` (both `https`), `remoteUrlUnchanged` is `true` (user never manually edited the remote), and `urlsMatch` is `false` (hostnames differ) — satisfying `protocolsMatch && remoteUrlUnchanged && !urlsMatch`. [3](#0-2) 
4. `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` rewrites `origin` to point at `attacker.example.com` without any user prompt.
5. The next time the user clicks "Push" in Desktop, their commits are sent to `attacker.example.com` instead of the legitimate GitHub host.

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
