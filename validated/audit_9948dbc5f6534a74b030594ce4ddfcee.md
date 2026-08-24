## Title
Unvalidated GitHub API `clone_url` passed directly to `git remote add` allows PR-fork attacker to inject an arbitrary transport (`ext::`) or option-like remote URL - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The report's underlying pattern is: a component (`VaultRegistry`) exposes a "trusted" gate (`addVault`, normally reachable only through a validating `VaultFactory`) but a privileged/side channel (`owner`) can push data into that gate without going through the validation the rest of the system relies on. The Desktop analog is `_findPullRequestBranch`/`_checkoutPullRequest` in `app/src/lib/stores/app-store.ts`, which takes a PR's `head.repo.clone_url` — a value that is fully attacker-controlled by anyone who opens a pull request from a fork — and feeds it straight into `addRemote()` → `git remote add <name> <url>` with no protocol/format validation, unlike other Git-invoking code paths in the same codebase (`clone.ts`) that do validate/guard their inputs.

### Finding Description
`_findPullRequestBranch` resolves the remote to fetch a PR branch from: [1](#0-0) 

`headCloneUrl` originates from the GitHub API's `pull_request.head.repo.clone_url` field, set by whoever opened the PR (see call sites): [2](#0-1) [3](#0-2) 

This value is passed unchanged to `addRemote`, which shells out to `git remote add name url` with no `--` separator and no allow-list on the URL scheme: [4](#0-3) 

Contrast this with `clone.ts`, which is aware that a hostile URL/path needs guarding: it checks `isClonePathSensitive()` for the destination path and inserts a `--` separator (`args.push('--', url, path)`) before the URL argument to stop it from being interpreted as a flag: [5](#0-4) [6](#0-5) 

`addRemote` has none of these mitigations. The only "verification" in the PR-checkout path is `urlMatchesRemote()` used to decide whether to reuse an *existing* remote — it is not a security check and does nothing to sanitize a *new* URL before it's registered as a remote: [7](#0-6) 
This `validateURL` scheme-allow-list (used for GitHub Enterprise server hostnames) is never applied to PR head clone URLs.

The proxy-resolution helper `envForProxy` also only recognizes `http(s)://` URLs and silently no-ops for anything else, so a non-HTTP(S) scheme quietly bypasses proxy handling too: [8](#0-7) 

The broken invariant mirrors the report exactly: the system assumes remote URLs reaching `git remote add`/fetch have been vetted as ordinary `https://`/`git@` GitHub URLs (the "approved factory" path), but a side channel — the PR API response, attacker-controlled — bypasses that assumption and reaches the same sink (`addRemote`) with no equivalent of `VaultFactory`'s validation.

### Impact Explanation
If `git` on the victim's machine has `protocol.ext.allow` set to `always`/`user` (a legacy but still-existing configuration some environments use, and historically a `git` default in older versions/distros), a `clone_url` of the form `ext::sh -c calc` registered as a remote and then fetched (`_fetchRemote` is called automatically right after `addRemote` in `_findPullRequestBranch`) results in arbitrary command execution on the victim's machine merely for opening/checking out a PR — no local access, no admin rights, and no unnatural user action beyond the normal "Checkout PR" click that Desktop already advertises as a one-click feature. Even without `ext::`, a URL value that isn't a `--`-guarded final positional argument could be crafted to smuggle git options into `remote add`, altering remote configuration in unexpected ways. This is a real code-execution/attacker-controlled-remote-response class matching the "Valid Impact" bar (attacker controls a GitHub API object / triggers execution via a link a user clicks to open the PR checkout flow).

### Likelihood Explanation
Likelihood depends on the victim's git/environment configuration for the `ext::` vector specifically, but the underlying design flaw — accepting and directly registering an attacker-supplied string from a PR API response as a git remote URL without any scheme allow-list — is unconditionally present and independently exploitable for remote-configuration corruption regardless of the `ext::` gate. Reaching it requires nothing more than the victim opening or checking out a PR from a fork inside Desktop, a routine, expected workflow (including the "Open PR from Desktop" deep-link handler at `openPullRequestFromUrl`), so the attack surface is broad and needs no social engineering beyond a normal PR interaction.

### Recommendation
Validate `head.repo.clone_url` (and any other API-supplied clone/remote URL) against the same allow-list applied to Enterprise server addresses (e.g., restrict to `https:`/`ssh:`/`git:`/`git@` GitHub-style forms) before calling `addRemote`, and always separate positional URL arguments from flags with `--` as already done in `clone.ts`. Reject anything that doesn't parse as a well-formed `IGitRemoteURL` via `parseRemote`.

### Proof of Concept
1. Attacker opens a pull request against a repository the victim has open in GitHub Desktop, from a "fork" whose `clone_url`, as delivered through the GitHub API response consumed by Desktop, is `ext::sh -c 'touch /tmp/pwned'` (requires `protocol.ext.allow` enabled locally, or otherwise a non-standard scheme the victim's git accepts).
2. Victim clicks "Checkout" for that PR in Desktop UI, calling `Dispatcher.checkoutPullRequest` → `_checkoutPullRequest` → `_findPullRequestBranch`. [9](#0-8) 
3. `addRemote(repository, forkRemoteName, headCloneUrl)` registers the malicious URL verbatim as a git remote with no scheme check.
4. `_fetchRemote` immediately fetches from the newly added remote, invoking the `ext::` transport (or equivalent) and executing the embedded command in the victim's environment.

I was unable to fully confirm within the available index whether Desktop's bundled `dugite`/git build sets any `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` restriction globally (no occurrences found in the searched files), so the exact exploitability of the `ext::` vector depends on the victim's git configuration; the unvalidated-URL-to-`addRemote` design flaw itself, however, is confirmed directly from source.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8613-8631)
```typescript
  public async _checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<void> {
    const prBranch = await this._findPullRequestBranch(
      repository,
      prNumber,
      headRepoOwner,
      headCloneUrl,
      headRefName
    )
    if (prBranch !== undefined) {
      await this._checkoutBranch(repository, prBranch)
      this.statsStore.increment('prBranchCheckouts')
    }
  }
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2039-2045)
```typescript
    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2508-2522)
```typescript
  public async checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    pullRequest: PullRequest
  ): Promise<void> {
    if (pullRequest.head.gitHubRepository.cloneURL === null) {
      return
    }

    return this.appStore._checkoutPullRequest(
      repository,
      pullRequest.pullRequestNumber,
      pullRequest.head.gitHubRepository.owner.login,
      pullRequest.head.gitHubRepository.cloneURL,
      pullRequest.head.ref
    )
```

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
```

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
}
```

**File:** app/src/lib/git/environment.ts (L93-104)
```typescript
export async function envForProxy(
  remoteUrl: string,
  env: NodeJS.ProcessEnv = process.env,
  resolve: (url: string) => Promise<string | undefined> = resolveGitProxy
): Promise<Record<string, string | undefined> | undefined> {
  const protocolMatch = /^(https?):\/\//i.exec(remoteUrl)

  // We can only resolve and use a proxy for the protocols where cURL
  // would be involved (i.e http and https). git:// relies on ssh.
  if (protocolMatch === null) {
    return
  }
```
