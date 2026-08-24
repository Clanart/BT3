## Finding: Missing scheme validation allows `ext::` transport command execution via PR fork clone URL

### Summary

`addRemote` and `urlMatchesRemote` never validate the URL scheme of a `GitHubRepository.cloneURL`. When checking out a pull request from a fork, the app-store code passes `pullRequest.head.gitHubRepository.cloneURL` straight into `git remote add` and later `git fetch` with no scheme allow-list, so a `clone_url` value using git's `ext::` (or similar) transport is accepted verbatim and will invoke an attacker-chosen shell command when the remote is fetched.

### Finding Description

The checkout-PR path is:

1. `Dispatcher.checkoutPullRequest` only checks `cloneURL !== null` before forwarding it. [1](#0-0) 

2. `AppStore._findPullRequestBranch` tries to find an existing remote whose URL matches via `urlMatchesRemote`, and if none is found, calls `addRemote` **unconditionally** with the raw `headCloneUrl`, then fetches it: [2](#0-1) 

3. `urlMatchesRemote` parses both URLs with `parseRemote`, which only recognizes a fixed set of `https?://`, `git@...`, `git:...`, `ssh://git@...` regex shapes; anything else (e.g. `ext::sh -c ...`) simply fails to parse and `parseRemote` returns `null`. [3](#0-2) [4](#0-3) 

Because `parseRemote` returns `null` for a non-matching scheme, `urlMatchesRemote` returns `false` — it does *not* accidentally accept `ext::` as a match. However, this "no match" result does not block anything: it just causes the fork-remote branch to be taken, which unconditionally calls `addRemote`: [5](#0-4) 

`addRemote` does no scheme filtering at all — it passes the URL straight through to `git remote add name url`. There is no `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` restriction set anywhere in the remote-operation environment helper: [6](#0-5) 

After adding the remote, the code calls `_fetchRemote`, which invokes plain `git fetch <remote-name>`: [7](#0-6) 

For a remote configured with an `ext::` URL, `git fetch` invokes the `ext` transport helper, which runs the given command through the shell — arbitrary command execution.

### Impact Explanation

If `pullRequest.head.gitHubRepository.cloneURL` is attacker-influenced (e.g. `ext::sh -c 'touch /tmp/pwned'`), checking out that PR's branch in GitHub Desktop results in:
- `git remote add fork-name ext::sh -c '...'` (harmless by itself), then
- `git fetch fork-name`, which executes the attacker's command via the `ext` transport helper.

This is full arbitrary code execution on the victim's machine, gated only on the victim opening/checking out a malicious PR.

### Likelihood Explanation

The realistic constraint is the origin of `cloneURL`. For `github.com`, `clone_url` in the API response is generated server-side in a fixed `https://github.com/<owner>/<repo>.git` shape and is not attacker-controllable to an arbitrary scheme. This gadget becomes reachable only when the "GitHub API object" itself is attacker-controlled — e.g. a malicious or compromised GitHub Enterprise Server endpoint the victim has added as an account, which can return arbitrary JSON (including a `clone_url` of `ext::...`) for any PR/repository object it serves. That scenario falls within the stated valid-impact scope ("attacker controls ... a GitHub API object"), but it is a narrower precondition than an ordinary github.com-hosted fork PR.

### Recommendation

- In `parseRemote` (`app/src/lib/remote-parsing.ts`), keep rejecting unrecognized schemes (it already does), but additionally make `addRemote`/`urlMatchesRemote`/`_findPullRequestBranch` treat "does not parse as a recognized `https`/`ssh` remote" as a hard rejection instead of silently falling through to `addRemote` with the raw string.
- Add an explicit allow-list check (`https:`, `ssh:`, `git:`) on any URL before it reaches `addRemote`/`setRemoteURL`/`fetch`, rejecting `ext::`, `file://`, and other non-network transports.
- Consider setting `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or `protocol.allow=never` plus explicit allows) in `envForRemoteOperation` so that even if an unexpected URL reaches `git`, the transport is refused at the git layer as defense-in-depth.

### Proof of Concept

Unit-test-style reproduction of the unchecked path (illustrative, not exact test harness):

```ts
// app/src/lib/remote-parsing.ts
parseRemote('ext::sh -c "touch /tmp/pwned"') // -> null, not "https"/"ssh"

// app/src/lib/repository-matching.ts
urlMatchesRemote('ext::sh -c "touch /tmp/pwned"', { name: 'origin', url: 'https://github.com/a/b.git' })
// -> false (no accidental match), BUT this result does not stop addRemote from being called below

// app/src/lib/stores/app-store.ts:_findPullRequestBranch
// remote === undefined (no match found)
await addRemote(repository, 'fork-owner', 'ext::sh -c "touch /tmp/pwned"')
// -> git(['remote', 'add', 'fork-owner', 'ext::sh -c "touch /tmp/pwned"'], ...)
await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
// -> git fetch fork-owner  => invokes ext transport => runs `sh -c "touch /tmp/pwned"`
```

This requires the `cloneURL` on `pullRequest.head.gitHubRepository` to have been populated with the `ext::...` string, which is only achievable if the API endpoint providing pull-request/repository data (e.g. a malicious/compromised GitHub Enterprise Server the user has connected to) supplies that value in its `clone_url` field.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L8641-8691)
```typescript
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

    const remoteRef = `${remote.name}/${headRefName}`

    // Start by trying to find a local branch that is tracking the remote ref.
    let existingBranch = gitStore.allBranches.find(
      x => x.type === BranchType.Local && x.upstream === remoteRef
    )

    // If we found one, let's check it out and get out of here, quick
    if (existingBranch !== undefined) {
      return existingBranch
    }

    const findRemoteBranch = (name: string) =>
      gitStore.allBranches.find(
        x => x.type === BranchType.Remote && x.name === name
      )

    // No such luck, let's see if we can at least find the remote branch then
    existingBranch = findRemoteBranch(remoteRef)

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

**File:** app/src/lib/repository-matching.ts (L90-100)
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
```

**File:** app/src/lib/remote-parsing.ts (L27-63)
```typescript
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]

/** Parse the remote information from URL. */
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
    }
  }

  return null
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

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```

**File:** app/src/lib/git/fetch.ts (L39-48)
```typescript
export async function fetch(
  repository: Repository,
  remote: IRemote,
  progressCallback?: (progress: IFetchProgress) => void,
  isBackgroundTask = false
): Promise<void> {
  let opts: IGitStringExecutionOptions = {
    successExitCodes: new Set([0]),
    env: await envForRemoteOperation(remote.url),
  }
```
