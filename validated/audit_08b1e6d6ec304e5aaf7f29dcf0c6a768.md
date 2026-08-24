## Analysis

The `updateRemoteUrl` function in `app/src/lib/stores/updates/update-remote-url.ts` checks several partial conditions before silently rewriting the local Git remote URL, but — like the Safe guard in TRST-M-6 that checked *some* invariants (guard slot, module-enabled, threshold) while missing a check that would prevent a new backdoor entity from being introduced — this function checks *some* conditions (protocol match, previous-URL match, current mismatch) but never validates that the new URL still points to the **same hostname/owner/repo identity** the user originally trusted, nor requires any user confirmation. [1](#0-0) 

## Title
Silent, unattended rewrite of a repository's Git remote URL from an attacker-influenced GitHub API object - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
`updateRemoteUrl` automatically replaces the user's configured Git remote URL (`origin`) with `apiRepo.clone_url` — a value obtained from a GitHub API response — whenever the protocol matches and the remote hasn't been "manually" changed relative to the previously cached `clone_url`. It never checks that the new URL keeps the same host, owner, or repository identity, and it performs this rewrite with no user prompt at all via `gitStore.setRemoteURL`.

### Finding Description
The invariant that should hold is: *the user's remote should never be silently repointed to a different host/repository without explicit consent.* `updateRemoteUrl` only verifies:
1. `protocolsMatch` — same URL scheme, and
2. `remoteUrlUnchanged` — the current remote still matches the previously known API `cloneURL`, and
3. `!urlsMatch` — the new API value differs from what's configured. [2](#0-1) 

None of these checks constrain the *hostname* of the incoming `apiRepo.clone_url`. `urlMatchesRemote`/`urlsMatch` are only used to detect whether the value changed, not to bound what it may change *to*. `apiRepo` (`IAPIRepository`) is populated straight from a GitHub/GHES API response tied to the repository record Desktop is tracking — a value which is exactly the "GitHub API object" category called out as attacker-controllable in the valid-impact list (e.g., via a malicious/compromised GitHub Enterprise Server the user has added an account for, a maintainer transferring/renaming the tracked repo to an attacker-owned namespace on the same host, or any server-side manipulation of the repository metadata returned to the client). Since `setRemoteURL` is invoked directly with no confirmation dialog, the effective "origin" the user pushes to and pulls from can change without any visible warning, analogous to how a new Safe module bypasses `checkAfterExecution` — the check only validates a subset of expected state and misses the actual dangerous transition (host/identity change).

### Impact Explanation
If a hostname/repo-identity change is allowed to pass silently, subsequent `git push`/`git fetch`/`git pull` operations initiated by the user (or Desktop's background fetch) would talk to the attacker's endpoint instead of the intended one. Depending on the credential helper and host-scoped keychain entries, this can result in credential/token exfiltration to the new host, or the user unknowingly pushing commits to (and thus disclosing code/history to) an attacker-controlled repository. This matches the "corruption of what the user commits or pushes" / "credential exfiltration" categories in the given Valid Impact criteria.

### Likelihood Explanation
This path only fires when a `GitHubRepository` is already associated with the local repo (so this is not attacker-initiated from scratch — it requires the app to already know the repo via the API) and the current invariant `remoteUrlUnchanged` (i.e., no local manual customization) holds. That is a normal, common state for the vast majority of GitHub Desktop users who cloned via Desktop and never hand-edited `.git/config`. The trigger is any refresh of `apiRepo` (e.g., periodic repository refresh calls that re-fetch repository metadata) returning a different `clone_url` than previously cached, which can occur due to repository transfer/rename on the server side or a malicious/compromised API response. No local access, admin rights, or unnatural user steps are required — it is entirely driven by server-returned metadata during Desktop's normal background repository-info refresh.

### Recommendation
Before calling `gitStore.setRemoteURL`, additionally validate that the new `clone_url`'s hostname (and ideally owner) matches an explicitly trusted value (e.g., the account's configured endpoint host), and/or surface an explicit, non-dismissable confirmation to the user when the remote's host or owner is about to change, rather than silently trusting `!urlsMatch` as the sole "should update" signal. At minimum, disallow the auto-update path when the hostname component differs from the previous one, treating a hostname change as a strictly separate/require-consent event distinct from an owner/name rename on the same host.

### Proof of Concept
1. User adds/clones a repository via Desktop from `https://github.com/acme/widgets`; `origin` is set to that URL and the associated `GitHubRepository.cloneURL` is cached as the same.
2. On a later repository-info refresh, the API response for that repository ID returns `clone_url: "https://evil-mirror.example.com/acme/widgets"` (e.g., due to a compromised/malicious server, a GHES misconfiguration, or the repository being effectively "moved" server-side) while the URL scheme (`https`) is unchanged.
3. `updateRemoteUrl` computes: `protocolsMatch = true` (both `https:`), `remoteUrlUnchanged = true` (current remote still equals last-known clone URL), `urlsMatch = false` (new host differs) — condition `protocolsMatch && remoteUrlUnchanged && !urlsMatch` is satisfied.
4. `gitStore.setRemoteURL('origin', 'https://evil-mirror.example.com/acme/widgets')` executes with no user prompt, silently repointing the local `origin` remote.
5. The next push/fetch by the user targets `evil-mirror.example.com`, sending code/credentials there instead of GitHub. [3](#0-2) 

The existing unit test above confirms the exact mechanics of the auto-update path (only object-level equality of URL structure is checked, not host trust boundary), corroborating that a host-changing update is accepted by this logic as long as protocol and "not manually changed" conditions hold.

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
