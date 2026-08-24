Found a concrete analog. `addRemote` in `app/src/lib/git/remote.ts` passes an attacker/API-controlled URL directly to `git remote add` without validating that it uses one of the expected `https`/`ssh` protocols:

```ts
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')
  return { url, name }
}
``` [1](#0-0) 

This is invoked with `headCloneUrl` taken straight from a GitHub pull-request API object's `head.repo.clone_url` field, with no protocol allow-listing anywhere in the call chain:

```ts
let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))
if (remote === undefined) {
  try {
    const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
    remote = await addRemote(repository, forkRemoteName, headCloneUrl)
``` [2](#0-1) 

This is reachable unprompted via the "Open in Desktop" custom-protocol PR flow: `parseAppURL` produces an `open-repository-from-url` action carrying an attacker-supplied `pr` number and `url`/repo target [3](#0-2) , which `dispatcher.openPullRequestFromUrl` feeds into `_checkoutPullRequest` → `_findPullRequestBranch` → `addRemote` using `pullRequest.head.repo.clone_url` from the API response [4](#0-3) . The PR object comes from `fetchPullRequest`, which just performs a GET and hands back the parsed JSON, so `head.repo.clone_url` is whatever the (possibly untrusted/enterprise) GitHub API endpoint returns [5](#0-4) .

By contrast, other clone paths in this codebase are already hardened: `clone.ts` blocks writes to sensitive filesystem locations [6](#0-5) , `sanitizeCloneName` blocks path-traversal in derived directory names [7](#0-6) , and `enterprise-validate-url.ts` enforces `https:` only for GHE URLs [8](#0-7) . None of that scheme validation is applied to `clone_url` before it reaches `git remote add`.

### Title
Unvalidated PR `clone_url` passed to `git remote add` enables Git transport-helper argument/URL injection - (File: `app/src/lib/git/remote.ts`)

### Summary
`addRemote` builds a `git remote add <name> <url>` argv without checking that `url` uses an allowed protocol (`https://` or `ssh://`/`git@`). The URL originates, unvalidated, from a GitHub Pull Request API response's `head.repo.clone_url` field, reachable through the `x-github-client://openRepo/...?pr=NNN` deep link handled by `parseAppURL`/`dispatchURLAction`.

### Finding Description
`_findPullRequestBranch` calls `addRemote(repository, forkRemoteName, headCloneUrl)` with `headCloneUrl` taken verbatim from the PR API object [9](#0-8) . `addRemote` itself performs no scheme/format check before invoking `git remote add name url` [1](#0-0) . Git recognizes special "remote helper" transports of the form `<transport>::<address>` (e.g. `ext::sh -c ...`, or custom helpers configured on the victim's system) as valid remote URLs. If a value such as `ext::sh -c "..."` or another remote-helper string ends up as a Git remote URL and any subsequent operation (`fetch`, `ls-remote`, etc.) is executed against that remote, Git will invoke the specified helper/command. This finding is invariant-broken because the code assumes `clone_url` values coming back from the GitHub API always resemble ordinary `https://`/`git@` URLs, but nothing in the ingestion path (`fetchPullRequest` → `openPullRequestFromUrl` → `_checkoutPullRequest` → `_findPullRequestBranch` → `addRemote`) enforces that assumption, unlike the sibling `clone.ts`/`enterprise-validate-url.ts` code paths, which do validate destination paths and protocols respectively.

### Impact Explanation
If reachable with a crafted `clone_url` (e.g. from a compromised/malicious GitHub Enterprise endpoint, a MITM'd API response, or any code path where PR data isn't strictly the trusted github.com API), this could result in arbitrary command execution on the victim's machine when Desktop subsequently fetches from the newly-added remote (`_fetchRemote` is called immediately after `addRemote` in the same function) [10](#0-9) . This matches the "attacker controls a GitHub API object... resulting in code execution" impact class.

### Likelihood Explanation
Likelihood is moderate-to-low for github.com itself (GitHub's API would need to be tricked into emitting such a `clone_url`), but the same code path is used for GitHub Enterprise Server accounts, where the API endpoint is attacker-influenced/self-hosted, and the "Open in Desktop" flow can be triggered purely by a user clicking a link/deep link that references a PR on such an endpoint — no local access or pre-existing malware required. The lack of any allow-list check makes this a real gap rather than a defense-in-depth nicety, especially compared to the explicit protocol checks already present for the enterprise sign-in URL and clone-destination hardening elsewhere in the same codebase.

### Recommendation
Validate `headCloneUrl` (and any other externally-sourced remote URL passed to `addRemote`/`setRemoteURL`) with `parseRemote`/an explicit allow-list of `https:` and `ssh:`/`git@` forms before calling `git remote add`, rejecting URLs containing `::` transport-helper syntax or unrecognized schemes, mirroring the pattern already used in `enterprise-validate-url.ts`.

### Proof of Concept
1. Trigger `x-github-client://openRepo/<repo>?pr=1` (or equivalent) against a GitHub Enterprise/API endpoint that returns a pull request object whose `head.repo.clone_url` is set to `ext::sh -c "touch /tmp/pwned"`.
2. `dispatcher.openPullRequestFromUrl` → `appStore._checkoutPullRequest` → `_findPullRequestBranch` calls `addRemote(repository, forkRemoteName, "ext::sh -c \"touch /tmp/pwned\"")`.
3. Immediately after, `_fetchRemote(repository, remote, ...)` executes `git fetch` against that remote, invoking the `ext::` helper and executing the embedded command.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L8613-8651)
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2045)
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
```

**File:** app/src/lib/api.ts (L1267-1279)
```typescript
  /**
   * Fetch a single pull request in the given repository
   */
  public async fetchPullRequest(owner: string, name: string, prNumber: string) {
    try {
      const path = `/repos/${owner}/${name}/pulls/${prNumber}`
      const response = await this.ghRequest('GET', path)
      return await parsedResponse<IAPIPullRequest>(response)
    } catch (e) {
      log.warn(`failed fetching PR for ${owner}/${name}/pulls/${prNumber}`, e)
      throw e
    }
  }
```

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
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

**File:** app/src/lib/remote-parsing.ts (L72-88)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
export function sanitizeCloneName(name: string): string | null {
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
