## Analysis

The confirmed pattern: `_findPullRequestBranch` in `app-store.ts` takes `headCloneUrl` sourced directly from the GitHub API's pull request object (`pullRequest.head.repo.clone_url`), which is attacker-controlled since any user can open a pull request from a fork with an arbitrary repository. When no existing remote matches the URL, this value is passed unvalidated straight into `addRemote()`, which shells out to `git remote add name url` [1](#0-0) , and `addRemote` itself performs no URL/scheme validation before invoking git [2](#0-1) . The remote is then fetched via `_fetchRemote` [3](#0-2) . Neither `addRemote` nor `envForRemoteOperation`/`envForProxy` set `GIT_ALLOW_PROTOCOL` or restrict `protocol.ext.allow`/`protocol.file.allow` [4](#0-3) , and no such restriction exists anywhere in the codebase (confirmed absent via search). This mirrors the smart-contract report's core flaw: a value from an untrusted, attacker-influenced source (fake `LPToken` instance / here, an attacker-controlled PR's `head.repo.clone_url`) is trusted and fed into a privileged, unchecked operation (`transferOut` / here, `git remote add` + `git fetch`).

I was not able to fully verify at what dugite/git version Desktop is pinned and whether that version's built-in default disallows `ext::`/`fd::` transports out-of-the-box (modern git disables these by default since 2.12 unless `protocol.ext.allow=always` is set, which somewhat mitigates but does not eliminate risk from `file://` or crafted `ssh://` URLs with `-oProxyCommand` style flags, since git's URL parser also permits argument-injection-like host strings in older git versions). This is a real limitation in my investigation — a Devin session with the ability to run `git --version` in the shipped Electron bundle and test actual `git remote add`/`fetch` behavior with crafted URLs (e.g. `ext::sh -c touch$IFS/tmp/pwned`, `-u./payload`, or `--upload-pack=touch /tmp/pwned`) would be needed to conclusively demonstrate code execution rather than just an unchecked-input design flaw.

### Title
Unvalidated PR fork clone URL from GitHub API is passed directly to `git remote add`/`fetch` - (File: app/src/lib/stores/app-store.ts)

### Summary
When a user checks out a pull request, `_findPullRequestBranch` reads `head.repo.clone_url` straight from the GitHub API pull-request payload — a field fully controlled by whoever opened the PR (an attacker can name their fork's remote/URL freely at PR-creation time, or via the `x-github-client://openRepo/...?pr=NNN` deep link which drives the same code path through `openPullRequestFromUrl`) — and passes it unmodified to `addRemote()` and later to `_fetchRemote()`, with no scheme allow-list, no `GIT_ALLOW_PROTOCOL` restriction, and no host/format validation beyond the loose `urlMatchesRemote` comparison used only to decide whether an *existing* remote should be reused.

### Finding Description
`_findPullRequestBranch` searches existing remotes for one whose URL structurally matches `headCloneUrl` via `urlMatchesRemote` (which merely compares hostname/owner/name via regex, not full URL string) [5](#0-4) . If none match, it directly calls `addRemote(repository, forkRemoteName, headCloneUrl)` [6](#0-5) . `addRemote` performs zero validation and just runs `git remote add name url` [2](#0-1) . The code then fetches that remote with `_fetchRemote` [3](#0-2) , and the environment set up for that operation only configures proxy/auth env vars for `http(s)://` URLs — it does not restrict git's transport protocols at all [7](#0-6) . This same `headCloneUrl` value is also reachable through the `x-github-client://openRepo/...` deep-link handler, since `openPullRequestFromUrl` calls `_checkoutPullRequest` with `pullRequest.head.repo.clone_url` fetched from the API in response to a link click [8](#0-7) .

This is directly analogous to the `LPToken` report: `supportMarket()` trusted an unverified, attacker-suppliable contract address and later `transferOut()` performed a privileged operation on it without additional checks. Here, `clone_url` is an attacker-suppliable string from an API object, and `git remote add`/`git fetch` are the privileged operations performed on it without protocol/format checks.

### Impact Explanation
If `clone_url` can be crafted to something other than a normal `https://`/`git@` remote (e.g., a `file://` path, `ext::` transport string, or an argument-injection-style value exploiting git's remote-URL parsing), a victim who merely opens a malicious pull request in Desktop (or clicks a deep link referencing that PR) could have git execute unintended local operations — ranging from reading/writing arbitrary local files (`file://` clone bypassing intended repo boundaries) to, in vulnerable git versions or configurations, command execution via the `ext::` remote helper transport. This lines up with the "Valid Impact" criteria: attacker controls a GitHub API object (the PR's head repo) and a link the user can click, and the result could be code execution or file access outside the repo.

### Likelihood Explanation
Likelihood is speculative without confirming the exact git/dugite version's default protocol allow-list and without a working PoC that git actually executes the crafted transport for this input. Since 2017 (git 2.12+) the `ext::` and `fd::` helpers are disabled by default unless explicitly allowed, which substantially reduces exploitability for the most direct RCE vector; however, the complete absence of any `GIT_ALLOW_PROTOCOL` allow-list or URL scheme validation in this specific code path means Desktop relies entirely on the bundled git's own defaults for protection, rather than defense-in-depth at the application layer.

### Recommendation
Validate `headCloneUrl` (and any other API-supplied clone URL) against an explicit allow-list of expected schemes (`https:`, `git@`/`ssh:`) before calling `addRemote`/`setRemoteURL`/fetch, reject `file://`, `ext::`, and other non-network transports, and additionally set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` in `envForRemoteOperation` so that even if a malicious URL slips through validation, git itself refuses to honor disallowed transports.

### Proof of Concept
Conceptual (not verified end-to-end due to tool limitations): an attacker opens a pull request from a fork whose repository's `clone_url` (as returned by the GitHub API) is crafted to abuse a non-`https` transport (e.g., `ext::sh -c "touch /tmp/pwned"` or a `file://` path pointing outside expected directories). When the victim, using GitHub Desktop, opens that PR via the "Checkout PR" UI action (`checkoutPullRequest` → `_checkoutPullRequest` → `_findPullRequestBranch`) or via a `x-github-client://openRepo/...&pr=NNN` link, Desktop calls `addRemote(repository, forkRemoteName, headCloneUrl)` [9](#0-8)  followed by `_fetchRemote` [3](#0-2)  with no scheme filtering, relying solely on the underlying git binary's own protocol defaults to prevent unintended transport execution.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8641-8659)
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

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
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
