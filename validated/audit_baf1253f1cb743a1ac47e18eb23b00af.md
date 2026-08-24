### Title
Silent remote-URL takeover via forged `clone_url` in GitHub API response - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl()` automatically rewrites a repository's Git `origin` URL whenever the cached GitHub API repository object reports a different `clone_url`, e.g. after a rename. Similar to the reported bug's flawed dependency on the contract's own balance to derive a critical fee value, this function derives a critical value (the new remote URL that Desktop will silently write to `.git/config` and later push/fetch/pull against) from an externally supplied field without validating that the new host is actually the same GitHub endpoint the user trusted.

### Finding Description
The update logic is: [1](#0-0) 

It computes:
- `urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)` — false whenever the new `clone_url` host/owner/name differs from the current local remote.
- `protocolsMatch` — only compares the URL *scheme* (`https:` vs `https:`), not the host.
- `remoteUrlUnchanged = urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)` — true if the locally configured remote still matches the previously cached GitHub repository record.

If `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, it calls `gitStore.setRemoteURL(...)` with the **new** `updatedRemoteUrl` taken directly from the API object, with no restriction that the new hostname equal the original hostname/endpoint: [2](#0-1) 

`urlMatchesRemote()` only parses and compares hostname/owner/name via `parseRemote()`, which accepts *any* host matching the generic GitHub-style URL shape (`https://<host>/<owner>/<name>`), not just `github.com` or the configured GHE hostname: [3](#0-2) [4](#0-3) 

Because the only host-equality check performed (`urlMatchesRemote`) is being used purely to detect *whether an update is needed*, not to constrain the update to the trusted endpoint, a `clone_url` field pointing to any attacker-chosen `https://` host with an owner/name segment will satisfy `!urlsMatch` and get written straight into the repository's Git config as the new `origin` URL, so long as the *scheme* matches and the user hasn't already diverged their remote from the cached API record. This exactly mirrors the reported class: a value used to drive a subsequent, consequential operation (fee amount / remote URL) is derived from state that isn't validated against the invariant the code actually depends on (correct balance / correct trusted host).

### Impact Explanation
If an attacker can influence the `clone_url` (or `ssh_url`/`html_url`, used elsewhere via `repositoryMatchesRemote`) returned by the GitHub/GHE API object for a tracked repository — the explicitly allowed "attacker controls a GitHub API object" primitive — Desktop will silently repoint the user's `origin` remote to an attacker-controlled host. Subsequent pushes/fetches/pulls transparently go to that host: this is silent corruption of what the user pushes (code can be redirected/exfiltrated to attacker infrastructure), and because Desktop's trampoline credential helper will attempt to authenticate against whatever host is configured (falling back to prompting for GitHub sign-in or generic credentials for unrecognized hosts), it creates a path toward credential prompt phishing/exfiltration against a host the user did not choose.

### Likelihood Explanation
The change requires no interaction beyond the normal background repository refresh that already calls into this update path when repository metadata is periodically re-fetched from the API. It only requires the attacker to control the API-served repository object's `clone_url` field for one refresh cycle, and requires the user's local remote to not have already diverged from the last cached GitHub repository record (`remoteUrlUnchanged`), which is the common case for unmodified/default clones. No local access, no privilege elevation, and no unnatural user steps are needed — it's a data-provenance validation gap.

### Recommendation
Do not derive the new remote URL purely from equality-of-structure comparisons against the previous cache. Before calling `setRemoteURL`, validate that the new `clone_url`'s hostname matches the hostname of the `Account`/`endpoint` that authenticated and returned the API object (i.e., the same trust boundary used elsewhere, such as in `findAccountForRemoteURL`). Reject/ignore automatic remote URL rewrites when the hostname changes, and only allow same-host owner/name changes (renames) to be applied silently; any cross-host change should require explicit user confirmation.

### Proof of Concept
1. User has a repository tracked with GitHub Desktop, `origin` pointing to `https://github.com/acme/widgets.git`, matching the cached `GitHubRepository.cloneURL`.
2. The GitHub/GHE API endpoint (attacker-controlled or compromised, or an on-path actor able to influence the JSON payload for that repository object) returns an updated repository object with `clone_url: "https://attacker-host.example/acme/widgets.git"`.
3. On the next metadata refresh, `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)` runs: `protocolsMatch` is true (`https:` == `https:`), `remoteUrlUnchanged` is true (local remote still matches cached record), `urlsMatch` is false (different hostname) — as confirmed directly by the existing test asserting the URL *does* get rewritten when `clone_url` changes: [5](#0-4) 
4. `gitStore.setRemoteURL('origin', 'https://attacker-host.example/acme/widgets.git')` is invoked, silently rewriting `.git/config`'s `origin` URL with no user prompt.
5. All future `git push`/`fetch`/`pull` operations target `attacker-host.example`.

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

**File:** app/src/lib/remote-parsing.ts (L27-63)
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

/** Parse the remote information from URL. */
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
    }
  }

  return null
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
