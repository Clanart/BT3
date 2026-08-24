### Title
Automatic remote URL rewrite from unpinned GitHub API `clone_url` field allows silent redirection of push/fetch target - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` rewrites a repository's local `origin` remote URL to whatever `clone_url` value is returned by a GitHub API repository object, with no validation that the new host is `github.com`, the account's own GitHub Enterprise host, or otherwise trusted. [1](#0-0) 
This is directly analogous to the reported `prepareCondition` flaw: a piece of externally-sourced, unauthenticated data (there, `priceOracle`/pricing; here, `clone_url`) is trusted and immediately written into a critical piece of persisted state (there, the condition's pricing oracle; here, the git remote used for all future pushes/fetches) without pinning it to a known-good value at creation time.

### Finding Description
`updateRemoteUrl` is invoked with a `GitStore`, the cached `GitHubRepository`, and an `apiRepo` object obtained from a GitHub API response. It computes whether to overwrite the local `origin` URL using three checks: protocol match, whether the *current* remote still matches the previously cached `gitHubRepository.cloneURL`, and whether the *new* `apiRepo.clone_url` differs from the current remote: [2](#0-1) 

None of these checks constrain the **hostname** of the incoming `clone_url` to a trusted value. The comparison helpers `urlMatchesRemote` / `urlsMatch` only compare hostname, owner, and name for *equality between two URLs* — they never validate that a hostname belongs to an allow-list of known GitHub endpoints: [3](#0-2) 

The underlying `parseRemote` regexes accept an arbitrary hostname component (`(.+)`) with no restriction to `github.com`/`*.ghe.com`/the account's configured Enterprise endpoint: [4](#0-3) 

Because the protocol check only compares protocol *scheme* (https vs ssh), not host, and the "unchanged" check only detects whether the remote was manually edited by the user (not whether the *new* target is legitimate), an API response whose `clone_url` field points at a completely different host will pass all guards and be written straight into the repository's git config via `gitStore.setRemoteURL`. [5](#0-4) 

If the JSON returned by the "GitHub API object" backing `apiRepo` is attacker-influenced — e.g., via a malicious/compromised GitHub Enterprise Server response, a man-in-the-middle on a non-pinned API endpoint, or a proxy that rewrites API responses — the `clone_url` field can be set to an attacker-controlled URL. The corrupted value here is exactly analogous to the report's "initial pricing data": a value sourced from an oracle-like external object (the GitHub API) that is trusted immediately and persisted without a permissioned/validated derivation step.

### Impact Explanation
Silently rewriting `origin` redirects all subsequent `git fetch`/`git pull`/`git push` operations initiated by the user to the attacker's endpoint. Because GitHub Desktop's credential trampoline matches accounts to hosts by comparing the *request* origin against stored account endpoints (`findGitHubTrampolineAccount`), an attacker who also controls a host matching the account's configured endpoint (e.g. spoofing/relaying the same hostname the user's GHE account uses, or luring the app into treating an attacker path as the account's endpoint) could receive git push traffic — including potentially commit content — intended for the legitimate repository. Even without credential exfiltration, this satisfies "silent corruption of what the user commits or pushes," since a `git push` after the swap would send data to a location the user never chose.

### Likelihood Explanation
This requires the attacker to influence the JSON returned by an API call the app makes for that specific repository (owner/name), which normally originates from the account's own trusted endpoint. This is a meaningfully narrower attack surface than the on-chain `prepareCondition` frontrunning primitive (no mempool equivalent exists here), and exploitation depends on an on-path/API-response-tampering capability against a specific GitHub/GHE endpoint (e.g., a malicious GHES instance, or a network position with a trusted TLS interception cert, or a compromised token used to rename/hijack an existing repository record). This is a real "git remote/proxy response" attacker primitive explicitly in-scope, but it is not trivially remote/unauthenticated like the original report, so likelihood is lower and depends on network or endpoint compromise being otherwise achievable.

### Recommendation
Do not trust `apiRepo.clone_url`'s hostname unconditionally. Before calling `gitStore.setRemoteURL`, verify the new URL's hostname matches either the existing remote's hostname or a known-good value derived from the account's configured endpoint (`getHTMLURL(account.endpoint)`), analogous to the report's fix of deriving the trusted identifier from a permissioned oracle rather than accepting caller-supplied data. Any cross-host change should require explicit user confirmation instead of silent auto-update.

### Proof of Concept
1. User has a repository whose `origin` remote and cached `GitHubRepository.cloneURL` both point to `https://github.example.com/org/repo`.
2. The API response used to refresh repository metadata (`apiRepo`) is tampered with (e.g., via a compromised/malicious Enterprise Server response or a MITM against the API host) to report `clone_url: "https://attacker.example/org/repo"`.
3. `updateRemoteUrl` is called: `protocolsMatch` is true (both https), `remoteUrlUnchanged` is true (the local remote still matches the last-known `cloneURL`), and `urlsMatch` is false (hostnames differ) — so the branch at [6](#0-5) 
executes and rewrites `origin` to `https://attacker.example/org/repo` with no user prompt or host allow-list check.
4. The next `git push`/`git fetch` performed by the user silently targets the attacker's host.

Note: I was not able to trace, within the indexed portion of `app-store.ts`, the exact call sites/trigger cadence of `updateRemoteUrl` (the file is large and the index only surfaced grep hit counts, not the calling context) — a Devin session with full file access would be needed to confirm the precise refresh trigger (e.g., periodic repository indicator refresh vs. explicit user action) and any additional pre-conditions.

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

**File:** app/src/lib/stores/updates/update-remote-url.ts (L22-44)
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

**File:** app/src/lib/remote-parsing.ts (L27-52)
```typescript
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]
```
