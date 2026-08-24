## Title
Deep-Link-Triggered Git `ext::` / `fd::` Transport RCE via Unrestricted Clone URL Scheme - (File: `app/src/lib/git/clone.ts`, `app/src/lib/git/environment.ts`)

### Summary
GitHub Desktop registers a custom protocol handler (`x-github-client://` / `github-mac://`) that is parsed by `parseAppURL` and can drive an `openrepo` action carrying an attacker-controlled `url` value straight into the "Clone repository" flow. Neither `parseAppURL`, `openOrCloneRepository`, nor the underlying `clone()`/`addRemote()` git wrappers validate or restrict the URL **scheme**. Git itself supports "smart" transport helpers such as `ext::` and `fd::` that, when not explicitly disabled via `GIT_PROTOCOL_FROM_USER=0` or `protocol.ext.allow`, will execute an arbitrary shell command supplied as part of the URL. Desktop never sets this hardening environment variable anywhere in `envForRemoteOperation`/`envForAuthentication`, so a crafted deep link (or a pasted/"Clone" URL, or a forked-PR head `clone_url`) that resolves to `ext::sh -c "<attacker command>"` results in code execution on `git clone`/`git remote add` + fetch.

### Finding Description
1. `parseAppURL` (`app/src/lib/parse-app-url.ts:66-125`) extracts the `openrepo` path segment as `url` with **no scheme allow-list** — it only special-cases `pr`/`branch` query params via regex, and the raw `url` is passed through untouched:
```
return { name: 'open-repository-from-url', url: parsedPath, branch, pr, filepath }
``` [1](#0-0) 

2. `main.ts`'s `open-url`/`--protocol-launcher` handlers forward this untrusted string straight to the renderer via `handleAppURL`/`sendURLAction`. [2](#0-1) 

3. `Dispatcher.openOrCloneRepository` pre-fills the Clone dialog with this attacker URL (`initialURL: url`), and once the user clicks "Clone" (a single, natural click after being lured by the deep link), `clone(url, path, options)` is invoked. [3](#0-2) 

4. `clone()` in `app/src/lib/git/clone.ts` validates the **destination path** (`isClonePathSensitive`) but does nothing to validate the **URL scheme**:
```
args.push('--', url, path)
await git(args, __dirname, 'clone', opts)
``` [4](#0-3) 
The `--` guard defeats *argument*-style injection (e.g. `--upload-pack=...`) but does nothing against a positional URL whose scheme itself is dangerous, such as `ext::sh -c "id > /tmp/pwned"` or `fd::2`.

5. `envForRemoteOperation`/`envForProxy` (`app/src/lib/git/environment.ts:76-139`) build the environment for every clone/fetch/push, but never set `GIT_PROTOCOL_FROM_USER=0` (or equivalent `protocol.ext.allow=never`) to disable "user" (`ext`, `fd`, `file`) protocols. No occurrence of `GIT_ALLOW_PROTOCOL`, `GIT_PROTOCOL_FROM_USER`, or `protocol.ext.allow` exists anywhere in the codebase. [5](#0-4) 

6. The same unguarded path exists for `addRemote()`, used when checking out a pull request from a fork (`_findPullRequestBranch`), where `headCloneUrl` comes from the GitHub API response and is passed unfiltered to git:
```
await git(['remote', 'add', name, url], repository.path, 'addRemote')
``` [6](#0-5) 
Once such a remote is configured, any subsequent fetch (`_fetchRemote`) resolves the URL from git config and executes the `ext::` helper. [7](#0-6) 

Existing hardening in this codebase (`isClonePathSensitive`, `sanitizeCloneName`, `resolveWithin`, `testForInvalidChars`) all defend against *path traversal* and *argument injection via leading dashes*, but none of them constrain the **URL protocol/scheme**, so the `ext::`/`fd::` vector is not covered by any existing guard.

### Impact Explanation
This breaks the invariant that opening a link or cloning a URL should only ever transfer repository data over a network Git transport (`https`, `ssh`, `git`) — never spawn an arbitrary local process. The corrupted value is the `url` field of `IOpenRepositoryFromURLAction` / the clone-dialog `url` state, which is attacker-controlled end-to-end from a clicked deep link. Successful exploitation yields full command execution as the Desktop user (arbitrary file write/read, credential theft, persistence), which matches the "code execution from an attacker-controlled deep link or git remote" category called out as valid impact in the report's Method: the primitive mirrors the smart-contract analog (an externally-supplied, unchecked value — approvedContract/URL — is trusted implicitly by a privileged operation), except here the "privileged operation" is a raw process spawn by Git.

### Likelihood Explanation
The only user interaction required is clicking a link (e.g., an `x-github-client://openrepo/ext::sh%20-c%20id` deep link on a phishing page, in a chat message, or in an "Open in Desktop" button on a malicious site) and then clicking "Clone" once the pre-populated dialog appears — no admin rights, no local access, no prior compromise, and no unnatural steps beyond what the "Open in Desktop" feature is designed to elicit. The PR-fork vector (`_checkoutPullRequest`) is a second, even lower-friction path since it triggers `addRemote`/`fetch` automatically once a user opens a crafted pull request notification — no explicit clone confirmation is required at all for the remote-add step.

### Recommendation
- Enforce an explicit allow-list of clone/remote URL schemes (`https:`, `http:`, `ssh:`, `git:`, and specific `user@host:path` SCP syntax) in `parseAppURL`, `openOrCloneRepository`, `clone()`, and `addRemote()`; reject anything else (in particular `ext::`, `fd::`, `file://`, `ssh` with `-oProxyCommand=` payloads, etc.).
- Set `GIT_PROTOCOL_FROM_USER=0` (or `protocol.ext.allow=never` / `protocol.file.allow=never`) in `envForRemoteOperation` for all remote-triggering git invocations (`clone`, `fetch`, `push`, `pull`, `ls-remote`, `remote add`) so git itself refuses "user" protocols regardless of any missed application-level check.
- Apply the same scheme validation to `headCloneUrl` before calling `addRemote` in `_findPullRequestBranch`.

### Proof of Concept
1. Attacker hosts a page/link with `x-github-client://openrepo/ext::sh%20-c%20%22touch%20/tmp/pwned%22`.
2. Victim (with GitHub Desktop installed and protocol handler registered) clicks the link.
3. `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'ext::sh -c "touch /tmp/pwned"', branch: null, pr: null, filepath: null }` — no scheme check rejects it. [1](#0-0) 
4. `openOrCloneRepository` opens the Clone dialog pre-filled with this URL. [8](#0-7) 
5. Victim clicks "Clone"; `clone()` runs `git clone -- 'ext::sh -c "touch /tmp/pwned"' <path>`. [4](#0-3) 
6. Because `GIT_PROTOCOL_FROM_USER` is never set to `0` anywhere in `envForRemoteOperation`, git's `ext` transport helper executes the embedded shell command, creating `/tmp/pwned` (in a real attack, running arbitrary attacker code) with the victim's privileges. [5](#0-4) 

**Note on verification limits:** I was unable to execute the actual git binary within this environment to confirm the exact git version's default `protocol.ext.allow` behavior, and I could not find any test file exercising scheme rejection for `clone()`/`addRemote()` in the indexed codebase (only clone-*path*-safety tests exist, not URL-scheme tests). If a start-a-Devin-session review of the full git dependency version pinned in `package.json` and any CI-level `GIT_PROTOCOL_FROM_USER` configuration is desired, that would help fully confirm exploitability versus this specific bundled Git version.

### Citations

**File:** app/src/lib/parse-app-url.ts (L118-124)
```typescript
    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
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
