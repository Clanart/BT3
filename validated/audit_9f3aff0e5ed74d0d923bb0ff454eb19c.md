Confirmed: there is no `GIT_ALLOW_PROTOCOL` restriction, no scheme allow-list, and no call to `parseRemote`/URL-shape validation anywhere in the pull-request checkout path before the remote URL reaches `git remote add` / `git fetch`.

### Title
Unvalidated PR head `clone_url` from GitHub API is passed directly to `git remote add`/`fetch`, enabling arbitrary command execution via Git transport helpers - (File: `app/src/lib/stores/app-store.ts`)

### Summary
When a user views or opens a pull request from a fork, GitHub Desktop takes `pullRequest.head.repo.clone_url` — a string returned by the GitHub API (or, via `x-github-client://openRepo` / `openrepo` deep links and `dispatcher.openPullRequestFromUrl`, effectively attacker-influenced input) — and passes it unmodified into `addRemote()` and then `git fetch`, without ever validating that it is a well-formed `https://`/`ssh://`/`git://` GitHub remote.

### Finding Description
`_findPullRequestBranch` in `app/src/lib/stores/app-store.ts` (around line 8633) receives `headCloneUrl` and, if no existing remote matches, does: [1](#0-0) 
This calls `addRemote()`: [2](#0-1) 
which shells out to `git(['remote', 'add', name, url], ...)` with no sanitation of `url`. The function is then immediately fetched via `_fetchRemote`: [3](#0-2) 

Unlike the "Clone repository" and "open repository from URL" flows, which run URL input through `parseRemote()` / `sanitizeCloneName()` (see `app/src/lib/remote-parsing.ts` and the hardening in `app/src/lib/git/clone.ts`), the PR-checkout path has no equivalent check. `envForRemoteOperation`/`envForProxy` only special-case `http(s)://` for proxy resolution and set no `GIT_ALLOW_PROTOCOL` allow-list: [4](#0-3) 

The trigger points that feed this sink are automatic, not user-confirmed:
- `dispatcher.checkoutPullRequest` (invoked simply by clicking a PR in the PR list) passes `pullRequest.head.gitHubRepository.cloneURL` straight through: [5](#0-4) 
- `openPullRequestFromUrl`, reachable via the `x-github-client://` protocol handler / `openrepo` deep link (`app/src/lib/parse-app-url.ts`), passes `pullRequest.head.repo.clone_url` from the raw API JSON with no format check: [6](#0-5) 

Git supports "remote helper" URL schemes such as `ext::<command>` (and others like `fd::`) that, when fetched, execute the specified local command. If the `clone_url` field can be made to contain such a value — e.g. a GitHub Enterprise Server instance that is compromised/malicious, a MITM'd API response, or a crafted third-party API-compatible endpoint the user has added as an account — Desktop will silently add that value as a git remote and fetch from it, executing arbitrary code with the privileges of the Desktop process. Even short of full RCE via `ext::`, an attacker-controlled clone_url pointing to `file://` paths can be used to read arbitrary local files into the checked-out fork branch (data exfiltration into the working tree), and there is nothing in the code path that constrains the scheme to `https/ssh/git`.

### Impact Explanation
If reachable, the corrupted value (`headCloneUrl`) drives an OS command execution primitive (`ext::`) or arbitrary local file read (`file://`) fully under attacker control, entirely without local access, admin rights, or pre-existing malware — matching the "code execution … outside the repo" and "silent corruption" criteria. The action requires only that the victim opens/checks out a PR (a completely normal Desktop action, not an "unnatural" user step), or that Desktop is pointed at (or receives an API response from) a malicious/compromised GitHub Enterprise endpoint.

### Likelihood Explanation
Likelihood is constrained by the fact that github.com itself server-generates `clone_url` values in a fixed `https://github.com/owner/repo.git` shape, so an attacker cannot inject an arbitrary scheme through ordinary forking on github.com. The realistic path requires either (a) a malicious/compromised GitHub Enterprise Server the user has signed into (Desktop trusts the endpoint's API responses without validating `clone_url` shape), or (b) an on-path/MITM tamperer of the API response, or (c) exploitation of `openrepo`/`x-github-client://` deep links combined with a GHE-style endpoint. This is a plausible but not trivially exploitable "untrusted GitHub API object" scenario — analogous in bug class (accepting an untrusted external value and using it in a privileged transfer/operation without validating shape/effect) to the Solidity report's failure to validate a token before trusting its `transferFrom` behavior.

### Recommendation
Before calling `addRemote`/fetching a PR head clone URL, validate it with the same `parseRemote()`/scheme allow-list already used for the "Clone repository" flow (`app/src/lib/remote-parsing.ts`), rejecting anything that isn't a recognized `https://`, `ssh://`, `git://`, or `git@host:` GitHub-style remote. Additionally consider setting `GIT_ALLOW_PROTOCOL=http:https:ssh:git` in `envForRemoteOperation` for all remote-touching git invocations to defense-in-depth block remote helper schemes like `ext::`/`fd::`.

### Proof of Concept
1. Point GitHub Desktop at a malicious/compromised GitHub-Enterprise-style API endpoint (or MITM the API response for a real endpoint).
2. Return a pull request JSON object whose `head.repo.clone_url` is `ext::sh -c "touch /tmp/pwned"` (or any attacker command).
3. In Desktop, open that PR (via the PR list or an `x-github-client://openrepo/...` deep link) and let Desktop attempt to check it out.
4. `_findPullRequestBranch` finds no matching existing remote, calls `addRemote(repository, forkRemoteName, 'ext::sh -c "touch /tmp/pwned"')`, then `_fetchRemote` runs `git fetch` against that remote, invoking the `ext` transport helper and executing the attacker command — with no confirmation dialog and no scheme validation anywhere in the call chain (`app-store.ts:8613-8721` → `git/remote.ts:28-37`). [7](#0-6)

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

**File:** app/src/lib/stores/app-store.ts (L8645-8660)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2507-2523)
```typescript
  /** Checks out a PR whose ref exists locally or in a forked repo. */
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
  }
```
