Based on my research, I found a strong analog in `app/src/lib/stores/updates/update-remote-url.ts`. I was unable to fully trace the exact call site in `app-store.ts` (3 matches found, but not read before running out of iterations), so I flag that as an open verification point below.

### Title
Automatic, unvalidated rewriting of a repository's git remote URL from GitHub API data - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`updateRemoteUrl()` silently overwrites the local `origin` remote URL with whatever `clone_url` is present on the `IAPIRepository`/`IAPIFullRepository` object returned by the GitHub API for the linked `GitHubRepository`, as long as the URL "protocol" (http vs ssh) is unchanged and the existing remote still matches the previously cached API `cloneURL`. This mirrors the reported bug class of "trusting an externally-supplied value without adequate validation before it is written to a security-relevant field" — here the externally-supplied value is a GitHub API object rather than a Solidity function argument, but the broken invariant is the same: an attacker-influenced value silently overrides a value the user relies on (their configured remote endpoint) without any host/ownership re-confirmation.

### Finding Description
`updateRemoteUrl` compares the current remote's URL to the repository's cached GitHub `cloneURL`, and if they still match (`remoteUrlUnchanged`) and the URL protocol is unchanged (`protocolsMatch`), but the new `clone_url` differs (`!urlsMatch`), it calls `gitStore.setRemoteURL(...)` to rewrite `origin` to the new URL — with no restriction that the new host must still be the same GitHub/GHE host the repository was originally associated with: [1](#0-0) 

The comparison function `urlMatchesRemote` treats hostname, owner, and repo name as independent fields — it does not enforce that only the `owner`/`name` portion can change while `hostname` must stay fixed: [2](#0-1) 

The `protocolsMatch` check only compares the URL scheme (`https:` vs `ssh:` etc.) via `URL.parse(...).protocol`; it does not compare hostname at all, so a same-protocol, different-host URL (e.g. `https://attacker.example/foo/bar`) satisfies `protocolsMatch`: [3](#0-2) 

The unit test confirms the update path is driven purely by whatever `clone_url` is present in the API repository object passed in, with no host allow-listing: [4](#0-3) 

The underlying git-level primitive that performs the actual mutation is `setRemoteURL`, which unconditionally runs `git remote set-url`: [5](#0-4) 

Because `IAPIRepository`/`IAPIFullRepository` objects originate from the GitHub API (or GitHub Enterprise API) response and are stored/synced through stores like `api-repositories-store.ts` and `repositories-store.ts`, this data is exactly the "GitHub API object" category called out in scope: if an API response is spoofed (compromised/malicious GHE server, MITM of the API endpoint, or a manipulated response for a repository the user has linked), `clone_url` can point anywhere with the same scheme, and Desktop will rewrite the user's `origin` remote to it without any prompt.

### Impact Explanation
If exploited, subsequent `git fetch`/`git push` operations transparently target the attacker-controlled remote (same code path as `push.ts`/`fetch.ts`, which just use `remote.url`/`remote.name` from the stored `IRemote`): [6](#0-5) [7](#0-6) 
This can (a) silently redirect what the user pushes to an attacker-controlled server (corruption of commit destination), and (b) since `envForRemoteOperation` supplies host-specific credentials/tokens for the request, redirecting to an attacker host can leak the credential material intended for the legitimate host if the credential helper/auth layer trusts the rewritten URL. This satisfies the "silent corruption of what the user commits/pushes" and potential "credential/token exfiltration" impact categories.

### Likelihood Explanation
Exploitation requires the attacker to control or spoof the API response (`clone_url`) for a repository the victim has already linked in Desktop — feasible for a malicious/compromised GitHub Enterprise server, a network position able to tamper with unauthenticated or misconfigured API traffic, or a scenario where the API endpoint itself is attacker-influenced. No local access, admin rights, or unnatural user steps are required beyond Desktop's normal background repository-info refresh; the function contains no confirmation dialog or host allow-list, so likelihood is moderate-to-high once the attacker has that API-response control. Exact confirmation of the automatic trigger path (I located 3 references to `updateRemoteUrl` in `app/src/lib/stores/app-store.ts` but could not fully inspect them before running out of tool calls) is an unresolved verification point.

### Recommendation
In `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts`), additionally require that the hostname of `updatedRemoteUrl` matches the hostname of the existing remote (not just the protocol) before calling `setRemoteURL`, and/or surface a user-facing confirmation when the GitHub API instructs Desktop to change a remote's URL to a different host.

### Proof of Concept
1. Link a repository in Desktop to a GitHub/GHE server such that `gitStore.defaultRemote.url` equals `gitHubRepository.cloneURL` (the normal, unmodified case — `remoteUrlUnchanged === true`).
2. Cause the app to receive an `IAPIRepository`/`IAPIFullRepository` object (via a compromised/spoofed API response) whose `clone_url` is `https://attacker.example/owner/name` — same scheme (`https:`), so `protocolsMatch` is `true`, and `urlMatchesRemote` returns `false` because hostname differs.
3. `updateRemoteUrl` executes `gitStore.setRemoteURL('origin', 'https://attacker.example/owner/name')`, confirmed to succeed unconditionally by the existing test at `app/test/unit/stores/updates/update-remote-url-test.ts:68-81` (same logic, only owner/name differ in the fixture — a different host would pass the same `protocolsMatch`/`urlsMatch` checks).
4. Next `git push`/`git fetch` initiated by the user now targets `attacker.example`.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L17-44)
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

**File:** app/src/lib/git/push.ts (L48-61)
```typescript
export async function push(
  repository: Repository,
  remote: IRemote,
  localBranch: string,
  remoteBranch: string | null,
  tagsToPush: ReadonlyArray<string> | null,
  options?: PushOptions,
  progressCallback?: (progress: IPushProgress) => void
): Promise<void> {
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```

**File:** app/src/lib/git/fetch.ts (L39-48)
```typescript
export async function fetch(
  repository: Repository,
  remote: IRemote,
  progressCallback?: (progress: IFetchProgress) => void,
  isBackgroundTask = false
): Promise<void> {
  let opts: IGitStringExecutionOptions = {
    successExitCodes: new Set([0]),
    env: await envForRemoteOperation(remote.url),
  }
```
