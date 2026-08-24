### Title
Silent, host-unverified rewrite of a repository's git remote URL from GitHub API data - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically rewrites a repository's `origin` remote URL whenever GitHub reports a different `clone_url` for the repo (e.g., after a rename), and it does so silently — no popup, banner, or log event is surfaced to the user, unlike every other sensitive git-state mutation in Desktop (force push, discard, undo commit, etc., all of which are gated by `askForConfirmation*` dialogs). Critically, the function never verifies that the new URL's **hostname** matches the original remote's hostname before accepting it.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` in `app-store.ts` during routine, non-interactive repository refresh: [1](#0-0) 

Its logic: [2](#0-1) 

It gates the rewrite on three checks:
- `protocolsMatch` — old and new URL use the same protocol (both `https:`)
- `remoteUrlUnchanged` — the *current* local remote still matches the previously cached `gitHubRepository.cloneURL` (i.e., the user hasn't manually edited it)
- `!urlsMatch` — the new API-provided `clone_url` differs from the current remote

None of these checks compares the **hostname** of the new `apiRepo.clone_url` against the original remote's hostname. `urlMatchesRemote` (used for `remoteUrlUnchanged`/`urlsMatch`) only asserts hostname equality *between the two values it's given*, not that the new value shares the old remote's host: [3](#0-2) 

So if `apiRepo.clone_url` — the "GitHub API object" field this function trusts — is `https://evil.example.com/owner/repoB` while the app previously tracked `https://github.com/owner/repoA`, all three gating conditions can still be satisfied (protocol still `https:`, old remote still matches its own cached value, new URL differs from old), and Desktop will execute: [4](#0-3) 

`GitStore.setRemoteURL` only calls `emitUpdate()` — an internal render-state notification — not any user-facing banner, toast, log entry, or confirmation dialog. Compare this to every other consequential mutation in the app (force push, discard changes, undo commit, checkout), which are wrapped in `askForConfirmationOn*` popups defined in `app-store.ts` and `dispatcher.ts`: [5](#0-4) [6](#0-5) 

There is no analogous confirmation, banner, or audit trail for an auto-rewritten remote — the exact "lack of event emission after a sensitive state change" pattern from the source report, applied to a case where the change silently redirects where the user's code and credentials go.

### Impact Explanation
If the `apiRepo.clone_url` field returned by the GitHub API can be attacker-influenced — via a compromised/malicious GitHub Enterprise server, a corporate proxy/MITM position on the API traffic (an explicitly in-scope vector per the task's "git remote/proxy response" category), or a spoofed API object — Desktop will silently reconfigure `origin` to point at an attacker-controlled host during a routine background refresh, with zero user-visible indication. Consequences:
- All subsequent `git push`/`git fetch` operations target the attacker's server.
- Git credential material (HTTPS Basic tokens, embedded auth headers) is transmitted to the attacker's host on the next push.
- Commits the user believes are being pushed to their real GitHub repository are instead silently redirected — the "silent corruption of what the user commits or pushes" outcome called out as valid impact.

### Likelihood Explanation
This path fires automatically as part of Desktop's normal repository-refresh flow (`repositoryWithRefreshedGitHubRepository`), requiring no unusual user action beyond having the app open with a tracked GitHub repository — refresh occurs periodically/on repository load. The trigger condition (repo name changing on GitHub / API returning a different `clone_url`) is a normal, legitimate feature Desktop already ships (see `changelog.json` entry "[Fixed] Update the remote url when a repository's name changes on GitHub"), meaning the vulnerable code path is exercised in production usage, not a rare corner case. The remaining uncertainty is how easily an attacker can inject an untrusted `clone_url` into a `fetchRepository` response in practice (this depends on TLS/network trust assumptions for GitHub.com vs. GHE, which I could not fully verify from local code alone).

### Recommendation
- Before calling `gitStore.setRemoteURL`, explicitly verify the new URL's hostname equals the existing remote's hostname (or belongs to an explicit allow-list tied to the account's configured endpoint), rejecting/ignoring updates that would change the host.
- Emit a visible, dismissible notification (banner/toast) and a log entry whenever Desktop auto-rewrites a repository's remote URL, so users can detect and react to unexpected changes — mirroring the `askForConfirmationOn*` pattern used elsewhere for consequential git operations.
- Consider requiring explicit user confirmation (similar to `UpstreamAlreadyExists`/`ConfirmForcePush`) before silently changing `origin`.

### Proof of Concept
1. Track a repository in Desktop whose GitHub API `fetchRepository` response can be influenced (compromised GHE server or MITM'd API response, per the accepted "git remote/proxy response" attacker model).
2. Craft the API repository object so `clone_url` = `https://attacker.example.com/owner/repoB` while keeping `private`/other fields consistent, and ensure the account's endpoint check still resolves.
3. Let Desktop's routine refresh call `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`, which finds `protocolsMatch === true`, `remoteUrlUnchanged === true` (local remote unchanged from cached value), `urlsMatch === false` (attacker URL differs) — the rewrite condition passes.
4. Observe `git remote -v` for the repo now shows `origin` pointing at `attacker.example.com`, with no dialog, banner, or notification presented to the user; the next `git push`/`fetch` transmits data/credentials to the attacker host.

Test coverage in `app/test/unit/stores/updates/update-remote-url-test.ts` confirms the rewrite logic and its gating conditions, but does not test for the missing hostname pinning: [7](#0-6)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

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

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2609-2618)
```typescript
    if (askForConfirmationOnForcePush) {
      this.showPopup({
        type: PopupType.ConfirmForcePush,
        repository,
        upstreamBranch: upstream,
      })
    } else {
      await this.performForcePush(repository)
    }
  }
```

**File:** app/src/ui/rebase/confirm-force-push.tsx (L36-69)
```typescript
  public render() {
    return (
      <Dialog
        title="Are you sure you want to force push?"
        dismissDisabled={this.state.isLoading}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onForcePush}
        type="warning"
      >
        <DialogContent>
          <p>
            A force push will rewrite history on{' '}
            <Ref>{this.props.upstreamBranch}</Ref>. Any collaborators working on
            this branch will need to reset their own local branch to match the
            history of the remote.
          </p>
          <div>
            <Checkbox
              label="Do not show this message again"
              value={
                this.state.askForConfirmationOnForcePush
                  ? CheckboxValue.Off
                  : CheckboxValue.On
              }
              onChange={this.onAskForConfirmationOnForcePushChanged}
            />
          </div>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup destructive={true} okButtonText="I'm sure" />
        </DialogFooter>
      </Dialog>
    )
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
