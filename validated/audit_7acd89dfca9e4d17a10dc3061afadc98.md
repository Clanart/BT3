## Title
Attacker-controlled GitHub API `clone_url` silently repoints a repository's git remote to an arbitrary host, corrupting where the user's next push/fetch goes - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
When Desktop refreshes a tracked repository's GitHub metadata, it compares the API's `clone_url` field against the locally configured remote and, if they differ, silently rewrites the local git remote's URL via `git remote set-url`. The safety check that gates this rewrite only verifies that the URL *protocol* (http vs ssh) is unchanged - it never verifies that the *host* stays the same. A GitHub/GHE API response (or a proxied/MITM'd API response for GitHub Enterprise) that returns a `clone_url` pointing at a different host is accepted at face value and silently becomes the new `origin` URL, redirecting all future pushes/fetches without any user confirmation.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` in `app-store.ts` every time Desktop refreshes GitHub repository info (on pull, fetch, account change, adding a repo, etc.): [1](#0-0) 

The update logic itself is: [2](#0-1) 

The only integrity checks performed before calling `gitStore.setRemoteURL` are:
1. `protocolsMatch` - compares `URL.parse(...).protocol` of the old and new URL (i.e., only `https:` vs `https:`, or bails out entirely for `ssh:`-style URLs that `URL.parse` can't handle).
2. `remoteUrlUnchanged` - confirms the *previously cached* `gitHubRepository.cloneURL` still matches the locally configured remote (i.e., the user hasn't manually repointed the remote).

Neither check compares the **hostname** or **owner/repo path** of the new URL against the old one. `urlMatchesRemote` (used only to compute `urlsMatch`, which gates whether an update is *needed*, not whether it's *safe*) does compare hostnames, but that comparison is inverted here - the code updates the remote precisely when the URLs (including hosts) *don't* match: [3](#0-2) 

So as long as `apiRepo.clone_url` shares the same URL scheme as the existing remote, Desktop will happily rewrite `origin` to point at any host, any owner, any repo name supplied by the API response - via the unguarded `setRemoteURL`: [4](#0-3) 

The `apiRepo` value comes directly from `api.fetchRepository(owner, name)`, which performs a GET to `repos/{owner}/{name}` against the account's configured endpoint - for GitHub Enterprise Server accounts this endpoint is user/organization configurable, and a compromised or spoofed GHES instance (or a network position able to tamper with that endpoint's TLS-terminated responses, e.g. via an enterprise TLS-inspecting proxy) fully controls the `clone_url` field returned: [5](#0-4) 

Unit tests confirm the exact unrestricted behavior - given a differing `clone_url` (even one pointing to a wholly different repo name), the local remote is repointed with no additional validation or prompt: [6](#0-5) 

### Impact Explanation
This breaks the invariant that "the remote a user configured is the remote pushes/fetches go to." A malicious or compromised GHE server (or a proxy/MITM that can tamper with the enterprise API traffic Desktop trusts) can cause Desktop to silently rewrite the tracked repository's `origin` remote to an attacker-controlled git host, sharing the same protocol as the original. Once rewritten:
- The user's next `git push` silently sends their commits to the attacker's server instead of the legitimate repository ("silent corruption of what the user commits or pushes").
- Because the endpoint no longer resolves to a known GitHub account, Desktop's trampoline credential helper falls back to prompting for or using generic/external credentials for the new host, which can be leveraged for further credential harvesting via a convincing fake prompt.
- This happens without any confirmation dialog, unlike normal remote-URL changes a user makes manually.

### Likelihood Explanation
The attack requires the attacker to control (or intercept/tamper with) the response of a GitHub Enterprise Server endpoint that a Desktop user has configured an account for - squarely within the allowed "git remote/proxy response" / "GitHub API object" threat model, and does not require local access, malware, or leaked credentials. It also does not need any unnatural user action: the refresh happens automatically on ordinary fetch/pull/account-refresh flows.

### Recommendation
In `updateRemoteUrl`, require that the new `clone_url`'s hostname (and ideally owner) match a trusted/expected value (e.g., the account's own endpoint host, or explicitly prompt the user) before calling `setRemoteURL`, rather than only checking that the URL scheme is unchanged. At minimum, reuse `urlMatchesRemote`/`urlsMatch`'s hostname comparison as a hard gate that blocks automatic updates across hostnames, and surface a confirmation UI when the GitHub API reports a cross-host `clone_url` change.

### Proof of Concept
1. User adds a repository in Desktop and signs into a GitHub Enterprise Server account; `origin` is `https://ghe.example.com/org/repo.git`.
2. The GHES instance is compromised/spoofed (or sits behind a tampering proxy) so that `GET repos/org/repo` returns `clone_url: "https://attacker.example.com/org/repo.git"` (same `https:` protocol).
3. Desktop performs a routine fetch/pull, triggering `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`.
4. `protocolsMatch` is true (both `https:`), `remoteUrlUnchanged` is true (user never touched the remote), `urlsMatch` is false (different host) → `gitStore.setRemoteURL('origin', 'https://attacker.example.com/org/repo.git')` executes silently.
5. The user's next push sends their commits to `attacker.example.com` with no warning shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

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

**File:** app/src/lib/api.ts (L972-988)
```typescript
  /** Fetch a repo by its owner and name. */
  public async fetchRepository(
    owner: string,
    name: string
  ): Promise<IAPIFullRepository | null> {
    try {
      const response = await this.ghRequest('GET', `repos/${owner}/${name}`)
      if (response.status === HttpStatusCode.NotFound) {
        log.warn(`fetchRepository: '${owner}/${name}' returned a 404`)
        return null
      }
      return await parsedResponse<IAPIFullRepository>(response)
    } catch (e) {
      log.warn(`fetchRepository: an error occurred for '${owner}/${name}'`, e)
      return null
    }
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
