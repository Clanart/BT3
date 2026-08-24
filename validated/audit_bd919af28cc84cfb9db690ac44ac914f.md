Confirmed: `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts:42-44`) calls `gitStore.setRemoteURL()` — which shells out to `git remote set-url` [1](#0-0)  — with zero hostname validation between the old and new URLs, only protocol-string equality and an "unchanged from cache" check.

### Title
Silent, unconfirmed rewrite of a repository's `origin` remote to an attacker-controlled host via forged GitHub API `clone_url` - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
When Desktop refreshes a repository's associated GitHub metadata, `repositoryWithRefreshedGitHubRepository` fetches the repo from the API and passes the response into `updateRemoteUrl`, which can silently execute `git remote set-url origin <apiRepo.clone_url>` without ever confirming the new URL points to the same host as before.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` calls `api.fetchRepository(owner, name)` and, if the repo already has an associated `GitHubRepository`, forwards the fresh API object straight into `updateRemoteUrl`: [2](#0-1) 

`updateRemoteUrl` decides whether to rewrite the local git remote based on three checks: [3](#0-2) 

- `protocolsMatch` only compares the URL scheme string (`https` vs `https`), not the host.
- `remoteUrlUnchanged` only verifies the *current* remote still matches the previously-cached `gitHubRepository.cloneURL` (i.e., the user hasn't hand-edited `origin`) — this is analogous to the Hats bug's stale "local storage state" being treated as the authority to decide whether an update is "safe."
- `!urlsMatch` is satisfied whenever the new `clone_url`'s hostname *or* owner/name differs from the current remote — the function does not distinguish "same host, renamed repo" (the intended, benign case) from "completely different host" (a hijack).

None of these three checks compare the new URL's hostname against the *old* URL's hostname. `urlMatchesRemote` (`app/src/lib/repository-matching.ts:90-118`) is only invoked to determine `urlsMatch`/`remoteUrlUnchanged`, and both invocations independently compare two URLs to each other — never asserting the API-returned host is consistent with what the local remote previously pointed to. If a malicious or compromised intermediary (the API response for a self-hosted GHES, or a MITM proxy in the request path, both of which are in-scope attacker models) returns a `clone_url` field pointing to a completely different host, this function will pass all three conditions and call `gitStore.setRemoteURL(...)`, silently overwriting `origin` to the attacker's URL — exactly like `changeHatToggle()` blindly trusting a new source-of-truth without first reconciling against the last verified state.

### Impact Explanation
Once `origin` is silently repointed, the next `git push` sends the user's code (and, via the credential helper/trampoline flow, the credential entered for that host) to the attacker's server [4](#0-3) , and the next `git pull`/fetch pulls attacker-controlled commits into the user's working tree without any dialog or confirmation — a silent corruption of what the user pushes/pulls, matching the "silent corruption of what the user commits or pushes" and "credential exfiltration" categories in the Valid Impact list.

### Likelihood Explanation
This path only triggers on the periodic/background GitHub-repository-refresh flow (`repositoryWithRefreshedGitHubRepository`, invoked e.g. on account change via `refreshSelectedRepositoryAfterAccountChange`) [5](#0-4) , which is automatic and not user-initiated, so exploitation requires the attacker to control or MITM one `fetchRepository` API response — plausible for GHES/proxy environments explicitly allowed by the task's threat model, though not achievable against an honest github.com endpoint over TLS.

### Recommendation
Before calling `gitStore.setRemoteURL`, additionally verify that the new URL's hostname (`parseRemote(updatedRemoteUrl)?.hostname`) matches the hostname of the current remote (or the account's known endpoint host); only allow automatic rewriting when the host is unchanged and merely the owner/name changed (the rename case this function was designed for), and require explicit user confirmation for any cross-host change.

### Proof of Concept
1. User has `origin` → `https://github.com/my-user/my-repo` and Desktop's cached `gitHubRepository.cloneURL` equals that same URL (the normal "unchanged" case) [6](#0-5) .
2. A compromised/MITM'd `fetchRepository` response (or malicious GHES) returns `clone_url: "https://evil.example.com/my-user/my-repo"`.
3. `updateRemoteUrl` computes `protocolsMatch = true` (both `https:`), `remoteUrlUnchanged = true` (cache matches current remote), `urlsMatch = false` (hostnames differ) → condition `protocolsMatch && remoteUrlUnchanged && !urlsMatch` is `true`.
4. `gitStore.setRemoteURL('origin', 'https://evil.example.com/my-user/my-repo')` runs, silently rewriting `.git/config`'s `origin` to the attacker's host with no user prompt, dialog, or hostname check.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/app-store.ts (L4916-4933)
```typescript
  /**
   * Refreshes the GitHub repository information for the currently selected
   * repository when the active account changes. This ensures that permission
   * information is updated after signing in/out.
   */
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
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
