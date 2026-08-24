## Title
Automatic remote-URL rewrite from an unauthenticated GitHub API field allows silent redirection of fetch/push targets - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts` automatically rewrites the local `origin` remote to whatever value is present in the `clone_url` field of a GitHub API repository object, with no validation that the new value points to the same host/service the user originally trusted. [1](#0-0) 

### Finding Description
The gating logic only checks:
1. that the URL protocol (`http`/`https`) is unchanged, and
2. that the *current* remote still matches the previously cached `gitHubRepository.cloneURL` (i.e. the user hasn't manually customized the remote). [2](#0-1) 

If both hold and the new `clone_url` differs from the current remote, the function calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` with the raw, unvalidated `apiRepo.clone_url` string — there is no check that the new host matches the previous host, matches the account's endpoint, or belongs to any allow-listed domain. `setRemoteURL` in turn runs `git remote set-url origin <url>` unconditionally. [3](#0-2) 

This is the same class of bug as the Symbiotic Relay report: a security-relevant value (the validator set / here, the trusted git remote) is mutated based on an externally-supplied object without re-verifying the invariant that made the original value trustworthy (stake ownership / here, "this host is the one the user originally cloned from"). The `IAPIRepository`/`IAPIFullRepository.clone_url` field is populated straight from a GitHub API (or GitHub Enterprise Server) HTTP response — i.e. it is exactly the kind of "GitHub API object" the task's valid-impact list calls out as attacker-controlled input (e.g. via a malicious/compromised GHES instance, a MITM'd API response, or any server able to answer the repository-metadata request Desktop makes).

The unit test suite confirms the unrestricted rewrite behavior — supplying an updated `clone_url` for the *same* repository test fixture is sufficient to make Desktop silently repoint `origin`: [4](#0-3) 

Nothing in `urlMatchesRemote`/`parseRemote` restricts the *new* URL's host to be related to the *old* one — those functions are only used to detect whether the URL already matches (to skip the update), not to bound what the replacement may be. [5](#0-4) 

### Impact Explanation
Once the `origin` remote is silently repointed to an attacker-controlled host:
- Subsequent user-initiated `git push` operations (via the UI, believing they are pushing to their known repository) send commits/branches to the attacker's server instead — exfiltrating source code/history.
- Subsequent `git fetch`/`pull` operations silently merge attacker-controlled content into the user's local repository under the same "origin" label the user trusts, with no UI indication that the remote endpoint changed — this is a direct instance of "silent corruption of what the user commits or pushes."
- Because the new host is untrusted, Desktop's credential/account matching (by exact origin) won't hand over the user's real GitHub token to the attacker host, but the code/data exposure and silent-redirect impact stand on their own.

### Likelihood Explanation
The rewrite path is triggered automatically during normal repository refresh whenever Desktop's cached knowledge of a `GitHubRepository`'s API data is refreshed and the API-reported `clone_url` differs from what's on disk (e.g. a real rename/transfer flow). No user interaction or confirmation dialog gates the actual `git remote set-url` call. Any entity able to influence the API response body for that repository record — a malicious/compromised GitHub Enterprise Server, or an on-path party able to alter that specific API response — can trigger the rewrite as long as protocol matches and the on-disk remote still equals the last known `cloneURL` (the common/default case for users who haven't hand-edited remotes).

### Recommendation
Do not blindly trust `clone_url` from the API for automatic remote rewriting. At minimum:
- Require that the new URL's hostname matches `gitHubRepository.endpoint`'s hostname (or a small allow-list derived from it) before calling `setRemoteURL`.
- Surface a confirmation prompt to the user before silently changing a configured remote URL, similar to other Desktop safety confirmations for destructive/security-relevant Git operations.
- Alternatively, make `updateRemoteUrl` internal-only and gate it behind an explicit "repository was renamed/transferred" signal (e.g. compare owner/name via `repository_id`, not just a string compare) rather than trusting the API-provided `clone_url` string wholesale.

### Proof of Concept
Using the existing test harness pattern in `app/test/unit/stores/updates/update-remote-url-test.ts`:
1. Set up a repository whose `GitHubRepository` metadata was fetched from a (compromised/malicious) endpoint, with `origin` pointing to `https://github.com/my-user/my-repo`.
2. Simulate a subsequent API refresh (or MITM'd response) where the same endpoint now returns `clone_url: 'https://attacker-controlled.example/my-user/my-repo'` (same protocol, `https`).
3. Call `updateRemoteUrl(gitStore, gitHubRepository, maliciousApiRepository)` — as the existing test at lines 68–81 demonstrates for a benign host change, `gitStore.currentRemote.url` becomes the attacker-supplied value with no validation of host identity. [4](#0-3) 
4. Any subsequent fetch/push from the Desktop UI now targets `attacker-controlled.example`.

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
