## Analog Found: Silent Git Remote URL Rewrite Based on Attacker-Influenced GitHub API Data

### Title
Automatic remote URL rewrite trusts unverified GitHub API `clone_url` and silently redirects push/fetch target - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The Isomorph bug stemmed from a security-relevant calculation trusting a stale/wrong state variable (`isoUSDLoaned` instead of `isoUSDLoanAndInterest`), letting an attacker bypass a margin check. The structurally similar pattern in GitHub Desktop is `updateRemoteUrl()`, which decides whether to silently rewrite a user's local git `origin` remote based on comparing a locally cached, unverified value (`gitHubRepository.cloneURL`) against the live GitHub API response (`apiRepo.clone_url`), rather than requiring explicit user confirmation of a genuinely trusted state change.

### Finding Description
`updateRemoteUrl` gates the rewrite on three conditions: `protocolsMatch`, `remoteUrlUnchanged`, and `!urlsMatch`: [1](#0-0) 

- `remoteUrlUnchanged` is computed by comparing the *locally stored* `gitHubRepository.cloneURL` (last time Desktop synced repo metadata) against the current git remote, using `urlMatchesRemote`, which only checks hostname/owner/name equality after parsing the URL structure: [2](#0-1) 

- If that "unchanged" check passes and the newly-fetched `apiRepo.clone_url` differs, Desktop calls `gitStore.setRemoteURL(...)` and rewrites the remote — with **no user prompt or confirmation**.

The `clone_url` field comes directly from a GitHub API repository object, which the task's threat model explicitly treats as attacker-influenceable input (e.g., a malicious/compromised GHES instance, a proxy tampering with API responses, or a repository transfer/rename scenario where Desktop still associates the local repo record by `dbID`). Because the guard only checks *protocol* and *hostname/owner/name equality with the previously cached value* — not any stronger authenticity signal (e.g., requiring the change to be tied to a verified rename webhook, or requiring user confirmation) — a party who can influence the API's `clone_url` for a repository record Desktop already tracks can cause Desktop to automatically re-point the user's `origin` remote to an attacker-controlled URL.

### Impact Explanation
If the remote is silently rewritten, subsequent `git push`/`git fetch`/`git pull` operations issued by Desktop will target the attacker-controlled remote instead of the user's intended one. This can lead to:
- Silent corruption/redirection of what the user pushes (matches "silent corruption of what the user commits or pushes" in the impact criteria).
- Credential exposure: Desktop's credential helper (`app/src/lib/trampoline/trampoline-credential-helper.ts`) will authenticate against whatever host the rewritten remote points to, potentially handing GitHub credentials/tokens to a look-alike host if the new `clone_url` hostname passes only structural parsing rather than strict endpoint verification.
- All of this happens with no explicit user-visible confirmation step, unlike the manual "protocol changed" guard comment which explicitly says Desktop should be conservative "in case they are relying on a specific" configuration — yet the URL-host-change case bypasses that same caution.

### Likelihood Explanation
The bug requires the attacker to influence the `clone_url` returned by the GitHub API (or a GHES/proxy) for a repository the user already has cloned and associated in Desktop's database — a scenario explicitly allowed under the task's threat model ("a GitHub API object ... or a git remote/proxy response"). No local access, admin rights, or social engineering steps are required beyond the user having Desktop open and periodically refreshing repository metadata (which happens automatically in normal background API refresh flows).

### Recommendation
- Require explicit user confirmation (a dialog) before ever rewriting an existing `origin` remote URL, especially when hostname changes.
- Verify the new `clone_url`'s hostname against the trusted account endpoint (`getHTMLURL(account.endpoint)`) before allowing an automatic rewrite, not just structural equality with the previous cached value.
- Log and surface remote URL changes to the user so silent redirection cannot go unnoticed.

### Proof of Concept
1. User clones `https://github.com/victim/repo.git`; Desktop stores `gitHubRepository.cloneURL = https://github.com/victim/repo.git`.
2. Desktop's background sync fetches repository info again and the API (compromised GHES, malicious proxy, or a race during a repo transfer) returns `clone_url: https://github.com/attacker/repo.git`.
3. In `updateRemoteUrl`: `protocolsMatch` is true (both `https:`), `remoteUrlUnchanged` is true (current remote still matches the previously cached `cloneURL`), and `urlsMatch` is false (new URL differs) — see the exact condition at: [3](#0-2) 
4. Desktop calls `gitStore.setRemoteURL('origin', 'https://github.com/attacker/repo.git')` without any user prompt, and the next push sends the user's commits to the attacker's repository.

Note: I was unable to trace the exact call sites in `app/src/lib/stores/app-store.ts` where `updateRemoteUrl` is invoked (the file content beyond the first line was not available through the index within my remaining tool budget), so I could not fully confirm what additional trust checks, if any, wrap this call in production flow (e.g., whether it's only invoked after other repository-identity verification). This limits certainty about real-world exploitability and I'd recommend a Devin session with full file access to verify the calling context before treating this as confirmed exploitable.

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
