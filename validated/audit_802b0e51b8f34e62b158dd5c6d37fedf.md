### Title
Unsanitized `clone_url` from GitHub API/PR objects reaches `git remote add` without the protections used in `clone()` - ([File: app/src/lib/git/remote.ts])

### Summary
`clone()` in `app/src/lib/git/clone.ts` treats a remote/clone URL as untrusted: it wraps the URL with a `--` end-of-options marker and builds a hardened `env` via `envForRemoteOperation(url)` before invoking git. Its sibling function `addRemote()` in `app/src/lib/git/remote.ts` receives the exact same class of value (a Git remote URL sourced from GitHub API objects such as a pull request's `head.repo.clone_url`) but applies **neither** protection when constructing the `git remote add <name> <url>` command line. This is the same "safe helper exists elsewhere, this call site skips it" pattern as the Elys `MustAccAddressFromBech32` report: one code path validates/guards untrusted input, a sibling path consuming the identical untrusted value does not.

### Finding Description
`addRemote` builds the git invocation directly from caller-supplied strings with no `--` separator and no call to `envForRemoteOperation`: [1](#0-0) 

Compare this to `clone()`, which explicitly documents and defends against the same class of untrusted-URL input: [2](#0-1) 

`addRemote` is called from `_findPullRequestBranch` in `app-store.ts` with `headCloneUrl`, a value taken directly from `pullRequest.head.repo.clone_url` — a field returned by the GitHub API (or, per the report's valid-impact scope, an untrusted/compromised GHES endpoint or proxy response) that Desktop does not otherwise validate: [3](#0-2) 

This path is reachable from an unprivileged, user-clicked action: opening a pull request from a fork, either via the in-app PR checkout flow (`dispatcher.checkoutPullRequest` / `_checkoutPullRequest`) or via the `x-github-client://openrepo/...?pr=N` deep link handled in `parseAppURL` and `openPullRequestFromUrl`: [4](#0-3) 

Once the remote is added with an attacker-influenced URL and the code subsequently fetches that remote (`_fetchRemote` inside `_findPullRequestBranch`), any git-supported remote scheme that the URL specifies (e.g. the `ext::` transport, which historically has been used to achieve arbitrary command execution through a crafted "clone URL" if `GIT_ALLOW_PROTOCOL`/`protocol.ext.allow` are not restricted) would be invoked with whatever hardening `envForRemoteOperation` would otherwise have provided — but that hardening is never applied for this call site, because `addRemote` doesn't call it.

### Impact Explanation
If the `clone_url` value reaching `addRemote` can be attacker-controlled (via a malicious/compromised GitHub Enterprise Server the victim has added an account for, or a MITM'd API/proxy response — both explicitly in-scope per the "Valid Impact" criteria), the missing hardening in `addRemote` means the same URL that would have been sanitized/contained in `clone()` is passed unmitigated into a git remote configuration and later fetch operation. This could lead to command execution on the victim's machine outside of Desktop's control, or at minimum silent corruption of the repository's remote configuration outside user awareness — matching the report's underlying broken invariant of "untrusted-input reaches a git-affecting primitive without the guard that exists in a parallel code path."

### Likelihood Explanation
Exploitation requires the `clone_url` field to be attacker-influenced, which for github.com itself is constrained (GitHub enforces owner/repo naming rules server-side). The scenario is credible mainly for GitHub Enterprise Server accounts or a compromised/MITM API response, both explicitly allowed attacker models in this task's "Valid Impact" section ("a GitHub API object ... or a git remote/proxy response"). I was not able to verify within the available context whether a process-wide `GIT_ALLOW_PROTOCOL` restriction is set elsewhere (e.g. globally in `core.ts` for all git invocations), which would reduce or eliminate the `ext::`-style RCE risk; this is a gap in my verification and should be checked directly in the repo before treating this as a confirmed RCE.

### Recommendation
Make `addRemote` consistent with `clone()`:
- Append `--` before the URL argument in the `git remote add` invocation to prevent flag/argument injection via a URL value beginning with `-`.
- Pass `env: await envForRemoteOperation(url)` (or equivalent protocol-restriction options) to the `git()` call in `addRemote`, matching the hardening already implemented in `clone.ts`.
- Audit all other call sites that build git argument arrays from GitHub-API-sourced URLs (`clone_url`, `ssh_url`) for the same missing `--`/`envForRemoteOperation` protections.

### Proof of Concept
Conceptual PoC chain (not independently executed against a live Desktop build):
1. Attacker controls (or MITMs) a GitHub Enterprise Server / API-compatible endpoint that the victim has an account configured for in Desktop.
2. Attacker crafts a pull-request API response (or a `x-github-client://openrepo/...?pr=N` deep link scenario feeding equivalent data) whose `head.repo.clone_url` is a URL using a dangerous transport scheme (e.g. `ext::...`) instead of a normal `https://`/`ssh://` URL.
3. Victim clicks "Checkout" on that PR (or the deep link) in Desktop.
4. `dispatcher.openPullRequestFromUrl` → `appStore._checkoutPullRequest` → `_findPullRequestBranch` → `addRemote(repository, forkRemoteName, headCloneUrl)` is invoked, adding the remote with no `--` guard and no restrictive `env`.
5. `_findPullRequestBranch` immediately calls `_fetchRemote` on the newly added remote, causing git to act on the attacker-controlled URL scheme without the hardening `clone()` normally applies. [5](#0-4)

### Citations

**File:** app/src/lib/git/remote.ts (L28-36)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
```

**File:** app/src/lib/git/clone.ts (L81-123)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)
```

**File:** app/src/lib/stores/app-store.ts (L8640-8660)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L8683-8691)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2048)
```typescript
  private async openPullRequestFromUrl(
    url: string,
    pr: string
  ): Promise<RepositoryWithGitHubRepository | null> {
    const pullRequest = await this.appStore.fetchPullRequest(url, pr)

    if (pullRequest === null) {
      return null
    }

    // Find the repository where the PR is created in Desktop.
    let repository: Repository | null =
      this.getRepositoryFromPullRequest(pullRequest)

    if (repository !== null) {
      await this.selectRepository(repository)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      log.warn(
        `Open Repository from URL failed, did not find or clone repository: ${url}`
      )
      return null
    }
    if (!isRepositoryWithGitHubRepository(repository)) {
      log.warn(
        `Received a non-GitHub repository when opening repository from URL: ${url}`
      )
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

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

    return repository
  }
```
