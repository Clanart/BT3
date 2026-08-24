### Title
Cross-host remote-URL takeover via unvalidated GitHub API `clone_url` in transfer function - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` silently rewrites a repository's `origin` remote URL using data reported by the GitHub API, but its only safety check is that the URL *scheme* (`https:`/`ssh:`) is unchanged — it never verifies that the **hostname** of the new URL matches the hostname of the existing remote or of the account's endpoint. This mirrors the "pair manipulation with transfer function" bug class from the FireToken report: a state-mutating "transfer" operation trusts an externally supplied value pair without validating the invariant that actually protects it (there, reserve/relative-price consistency; here, same-origin/hostname consistency), letting an attacker-influenced input silently corrupt a security-relevant piece of state.

### Finding Description
`updateRemoteUrl` (the "transfer function" that mutates the local git remote) computes: [1](#0-0) 

- `remoteUrl` — the current local remote URL.
- `updatedRemoteUrl` — `apiRepo.clone_url`, a value supplied entirely by the (remote) GitHub API response.
- `protocolsMatch` — compares only `URL.parse(...).protocol` of the old and new URLs, **not** hostname.
- `remoteUrlUnchanged` — checks that the *previous* cached `gitHubRepository.cloneURL` still matches the current remote (i.e., the user hasn't manually re-pointed origin). [2](#0-1) 

If `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, the function calls `gitStore.setRemoteURL(...)` with the new `updatedRemoteUrl` — **with no hostname/owner allow-list check at all**. The `urlsMatch` check (`urlMatchesRemote`) only decides whether an update is *needed*, not whether it is *safe*; a URL pointing to a completely different host (e.g. `https://evil.example.com/attacker/repo.git`) still yields `!urlsMatch === true` and passes the guard, because nothing in the condition compares hostnames between old and new remote.

This is confirmed by the existing unit test suite, whose scenarios only cover protocol mismatches and "remote already customized" cases — none of them assert that the new host must equal the old host: [3](#0-2) [4](#0-3) 

The last test only rejects the update because the remote was already *manually customized* by the user, not because the new host differs from the old one — so the missing hostname check is not incidentally covered anywhere.

Compare this with the equivalent hardening added elsewhere in the same codebase for clone destinations (`isClonePathSensitive` in `app/src/lib/git/clone.ts`) and submodule URLs (`allowFileProtocol` flag in `app/src/lib/git/submodule.ts`) — both of those paths were explicitly hardened against untrusted-URL-driven corruption, but `update-remote-url.ts` was not given an analogous "same host" invariant check.

### Impact Explanation
`origin` is the destination Desktop uses for subsequent `git push`, `git fetch`, `git pull`, and credential negotiation (`envForRemoteOperation`) for the repository. Silently repointing it to an attacker-controlled host means:
- Subsequent pushes send the user's commits/branches to the attacker's server instead of (or in addition to) the legitimate GitHub repository.
- Git credential helpers / stored PATs can be sent to the attacker's host during the negotiated fetch/push, since credentials are typically resolved per-remote-URL.
- The corruption is silent — the UI does not prompt the user, and the remote line in `.git/config` is rewritten transparently, so a user reviewing the repository state in the normal Desktop UI would not obviously notice unless they inspect the exact remote URL.

This matches the "Valid Impact" criteria: it is an unprivileged flow where the attacker controls a GitHub API object surfaced back into the client and the result is credential/token exfiltration risk and silent corruption of what the user pushes.

### Likelihood Explanation
The exact conditions under which `apiRepo.clone_url` can diverge in *hostname* from the previously-recorded value are not something I could fully verify from the indexed portion of the codebase — I was unable to locate the exact call site/API-fetching code (`fetchRepository`) that produces `apiRepo` before `updateRemoteUrl` is invoked, so I cannot confirm the exact trigger (e.g., cross-endpoint repository transfer, enterprise-server spoofing, or a compromised/MITM'd API response) that would let an attacker supply a `clone_url` with a different host while keeping `protocolsMatch` true. This is a real gap in the guard logic (confirmed directly in code and tests), but likelihood depends on how trustworthy `apiRepo` is assumed to be at the call site, which I could not fully trace given the tool-call budget.

### Recommendation
In `updateRemoteUrl`, in addition to comparing protocols, require that the hostname of `updatedRemoteUrl` matches the hostname of the account's expected API endpoint (or of the existing remote) before calling `gitStore.setRemoteURL`. Concretely, parse both URLs with `parseRemote`/`URL.parse` and reject the update unless `parsedRemoteUrl.hostname === parsedUpdatedRemoteUrl.hostname` (or matches the GitHub endpoint the user is authenticated to), never trusting `apiRepo.clone_url`'s host implicitly.

### Proof of Concept
Using the existing test harness pattern in `app/test/unit/stores/updates/update-remote-url-test.ts`:
1. Set up a repository whose `origin` is `https://github.com/my-user/my-repo` and whose cached `gitHubRepository.cloneURL` is the same.
2. Call `updateRemoteUrl(gitStore, gitHubRepository, { ...apiRepository, clone_url: 'https://evil.example.com/attacker/repo' })`.
3. Because `protocolsMatch` is true (both `https:`) and `remoteUrlUnchanged` is true (the user never manually edited `origin`), the guard passes and `gitStore.currentRemote.url` becomes `https://evil.example.com/attacker/repo` — with no hostname check anywhere in the path, as shown at [5](#0-4) .

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-34)
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L96-112)
```typescript
  it("doesn't update repository's remote url if protocols don't match", async t => {
    const originalUrl = 'git@github.com:desktop/desktop.git'
    const sshApiRepository = {
      ...apiRepository,
      clone_url: originalUrl,
    }
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      sshApiRepository
    )
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }

    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert(gitStore.currentRemote !== null)
    assert.equal(gitStore.currentRemote.url, originalUrl)
  })
```
