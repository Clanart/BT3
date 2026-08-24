## Title
Attacker-controlled fork parent `cloneURL` passed unsanitized to `git remote add`/`set-url` enables `ext::`/`file://` transport-helper command execution — (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.ensureUpstreamRemoteURL`, `GitStore.addUpstreamRemoteIfNeeded`, and `GitStore.updateExistingUpstreamRemote` take the `cloneURL` of a `GitHubRepository.parent` object (data sourced from a GitHub/GHES API response) and pass it, unvalidated, straight into `addRemote`/`setRemoteURL`, which shell out to `git remote add <name> <url>` / `git remote set-url <name> <url>`. No scheme allow-listing (e.g. restricting to `http(s)://`/`ssh://`/`git://`) is performed anywhere along this path, and GitHub Desktop does not set `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` to restrict dangerous transports such as `ext::` or `file://`.

### Finding Description
The relevant call chain is:

- `GitStore.addUpstreamRemoteIfNeeded` unwraps `parent.cloneURL` and calls `addRemote(this.repository, UpstreamRemoteName, url)` with no validation. [1](#0-0) 
- `GitStore.ensureUpstreamRemoteURL` does the same, calling `addRemote`/`setRemoteURL` with the caller-supplied `remoteUrl` and no validation. [2](#0-1) 
- `GitStore.updateExistingUpstreamRemote` also unwraps `parent.cloneURL` directly and forwards it to `setRemoteURL`. [3](#0-2) 
- `addRemote`/`setRemoteURL` in the git layer place the string directly as an argv element for `git remote add`/`git remote set-url`, with no scheme check. [4](#0-3) 
- `AppStore._convertRepositoryToFork` is a concrete production caller that plumbs an `IAPIFullRepository` (`fork.clone_url`) — API-controlled data — into `gitStore.setRemoteURL` and `gitStore.ensureUpstreamRemoteURL`. [5](#0-4) 

None of the code paths verify the URL's transport scheme is one of the expected `https`/`ssh`/`git` before calling into git. Separately, `app/src/lib/git/environment.ts`, which builds the environment used for all remote-touching git invocations, only configures proxy/auth env vars and does not set `GIT_ALLOW_PROTOCOL` or pass `-c protocol.ext.allow=never` / `-c protocol.file.allow=never` to git. [6](#0-5) 

Git itself has a protocol allow-list mechanism (`protocol.<name>.allow`, default `user` for `ext`/`file`) intended to stop *automatically discovered* URLs (e.g. from `.gitmodules` fetched as part of untrusted repo content) from invoking dangerous transports. However, that mitigation is scoped to distinguishing "automatically discovered from repo content" vs "provided by the local user/tool," not to validating the *origin* of the string before it is written into `.git/config`. Once Desktop writes an `ext::`/`file://` URL into `remote.upstream.url` via `git remote add`/`set-url`, subsequent `git fetch upstream` treats that config value as ordinary, locally-configured remote configuration — the same trust tier as if a human had typed it — and Git's built-in submodule-only protections do not apply here.

### Impact Explanation
If an attacker can get a `GitHubRepository.parent.cloneURL` value that Desktop trusts to contain a transport-helper string (most plausibly via a malicious/compromised GitHub Enterprise Server the victim has added as an account, since `clone_url` for `github.com`-hosted repos is normally not attacker-formattable, but is fully attacker-controlled if the API server itself is malicious), Desktop will:
1. Write that string as the `upstream` remote URL via `addRemote`/`setRemoteURL` (`app/src/lib/git/remote.ts`).
2. On any subsequent fetch of that remote (automatic background fetch or user-initiated), git will invoke the `ext::`/`file://` transport helper, resulting in arbitrary process execution under the user's privileges.

This matches the "code execution" and "silent corruption of git configuration" categories of valid impact defined in scope, driven purely by an attacker-controlled GitHub API object.

### Likelihood Explanation
Exploitation requires the victim to have configured an account against a malicious or compromised GitHub Enterprise Server (or a MITM'd/compromised github.com API response) that returns a poisoned `parent.clone_url` for a forked repository, and for Desktop to reach `_convertRepositoryToFork`, `addUpstreamRemoteIfNeeded`, or `updateExistingUpstreamRemote` for that repository. This is a realistic but not trivial precondition (it excludes ordinary github.com forks, whose `clone_url` is server-generated and scheme-constrained), which places likelihood at low-to-moderate, contingent on the GHES/malicious-server threat model being in scope.

### Recommendation
Before calling `addRemote`/`setRemoteURL` with any API-sourced or otherwise externally-influenced URL (in `git-store.ts`'s `addUpstreamRemoteIfNeeded`, `ensureUpstreamRemoteURL`, `updateExistingUpstreamRemote`, and `app-store.ts`'s `_convertRepositoryToFork`/PR-fork-remote code), validate that the URL's scheme is restricted to an explicit allow-list (`https:`, `http:`, `ssh:`, `git:`, and the scp-like syntax) and reject anything else (`ext::`, `file://`, `fd::`, etc.). Additionally, consider defensively setting `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or the equivalent `-c protocol.allow=never -c protocol.https.allow=always ...`) in `envForRemoteOperation`/`git()` for all remote-touching invocations as defense-in-depth.

### Proof of Concept
1. Configure a test/malicious GitHub Enterprise Server endpoint account in Desktop (or otherwise obtain an `IAPIFullRepository`/`GitHubRepository.parent` whose `clone_url`/`cloneURL` is `ext::sh -c "touch /tmp/pwned"`).
2. Trigger a code path that calls `gitStore.ensureUpstreamRemoteURL(parent.cloneURL)` or `AppStore._convertRepositoryToFork` with that malicious parent object — e.g. opening a repo that Desktop detects as a fork of that malicious parent.
3. Observe `.git/config` now contains `[remote "upstream"] url = ext::sh -c "touch /tmp/pwned"`.
4. Trigger any fetch of the `upstream` remote (manual "Fetch" or Desktop's periodic background fetch).
5. Observe `/tmp/pwned` created, confirming arbitrary command execution.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1347-1355)
```typescript
    const url = forceUnwrap(
      'Parent repositories are fully loaded',
      parent.cloneURL
    )

    this._upstreamRemote =
      (await this.performFailableOperation(() =>
        addRemote(this.repository, UpstreamRemoteName, url)
      )) ?? null
```

**File:** app/src/lib/stores/git-store.ts (L1364-1380)
```typescript
  public async ensureUpstreamRemoteURL(remoteUrl: string): Promise<void> {
    await this.performFailableOperation(async () => {
      try {
        await addRemote(this.repository, UpstreamRemoteName, remoteUrl)
      } catch (e) {
        if (
          e instanceof DugiteError &&
          e.result.gitError === GitError.RemoteAlreadyExists
        ) {
          // update upstream remote if it already exists
          await setRemoteURL(this.repository, UpstreamRemoteName, remoteUrl)
        } else {
          throw e
        }
      }
    })
  }
```

**File:** app/src/lib/stores/git-store.ts (L1678-1695)
```typescript
  public async updateExistingUpstreamRemote(): Promise<void> {
    const gitHubRepository = forceUnwrap(
      'To update an upstream remote, the repository must be a GitHub repository',
      this.repository.gitHubRepository
    )
    const parent = forceUnwrap(
      'To update an upstream remote, the repository must have a parent',
      gitHubRepository.parent
    )
    const url = forceUnwrap(
      'Parent repositories are always fully loaded',
      parent.cloneURL
    )

    await this.performFailableOperation(() =>
      setRemoteURL(this.repository, UpstreamRemoteName, url)
    )
  }
```

**File:** app/src/lib/git/remote.ts (L28-64)
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

/** Removes an existing remote, or silently errors if it doesn't exist */
export async function removeRemote(
  repository: Repository,
  name: string
): Promise<void> {
  const options = {
    successExitCodes: new Set([0, 2, 128]),
  }

  await git(
    ['remote', 'remove', name],
    repository.path,
    'removeRemote',
    options
  )
}

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

**File:** app/src/lib/stores/app-store.ts (L8969-8991)
```typescript
  public async _convertRepositoryToFork(
    repository: RepositoryWithGitHubRepository,
    fork: IAPIFullRepository
  ): Promise<Repository> {
    const gitStore = this.gitStoreCache.get(repository)
    const defaultRemoteName = gitStore.defaultRemote?.name
    const remoteUrl = gitStore.defaultRemote?.url
    const { endpoint } = repository.gitHubRepository

    // make sure there is a default remote (there should be)
    if (defaultRemoteName !== undefined && remoteUrl !== undefined) {
      // update default remote
      if (await gitStore.setRemoteURL(defaultRemoteName, fork.clone_url)) {
        await gitStore.ensureUpstreamRemoteURL(remoteUrl)
        // update associated github repo
        return this.repositoriesStore.setGitHubRepository(
          repository,
          await this.repositoriesStore.upsertGitHubRepository(endpoint, fork)
        )
      }
    }
    return repository
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
