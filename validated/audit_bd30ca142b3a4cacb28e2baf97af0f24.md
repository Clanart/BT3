Confirmed: `resolveCloneInfo()` in `clone-repository.tsx` only looks up richer clone info when `lastParsedIdentifier` (a parsed owner/name pair via `parseRemote`/`parseRepositoryIdentifier`) and a matching account exist. If the URL doesn't match any of `remoteRegexes` in `remote-parsing.ts` (e.g. a non-standard scheme like `ext::sh -c "..."`), `lastParsedIdentifier` is `null` and the code falls straight through to `return { url }` unmodified — the raw attacker-supplied string is handed to `cloneImpl` → `_clone` → `clone()` in `app/src/lib/git/clone.ts`, which only checks the *destination path* for sensitivity (`isClonePathSensitive`) and never validates the *protocol/scheme* of `url` before splicing it into `args.push('--', url, path)` for `git clone`. The same unrestricted-URL pattern exists in `addRemote()` (`app/src/lib/git/remote.ts`) which is fed `pull_request.head.repo.clone_url` straight from GitHub API responses in `dispatcher.ts` (`_findPullRequestBranch`, `_checkoutPullRequest`) with no scheme allow-list, and no `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` restriction is ever set in `environment.ts`.

### Title
Unvalidated remote/clone URL scheme allows git `ext::`/`fd::` transport command execution via deep link or malicious API response - (File: app/src/lib/git/clone.ts, app/src/lib/git/remote.ts, app/src/lib/remote-parsing.ts)

### Summary
GitHub Desktop lets multiple untrusted sources supply an arbitrary string as a git remote/clone URL — the `x-github-client://openRepo/<url>` deep link (`app/src/lib/parse-app-url.ts`), and a pull request's `head.repo.clone_url` field returned by a GitHub API server (`app/src/ui/dispatcher/dispatcher.ts`). Neither the deep-link parser nor `clone()`/`addRemote()` restrict the URL to a safe set of protocols (https/ssh/git) before it is spliced verbatim into a `git clone`/`git remote add` argument list and later used for `fetch`/`push`. Git supports "remote helper" pseudo-protocols such as `ext::<command>` and `fd::` that spawn an arbitrary shell command as part of a clone/fetch. Because Desktop never sets `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` nor rejects such schemes at the application layer, a crafted URL reaching these functions results in local command execution outside any Desktop-imposed sandbox.

### Finding Description
- `parseAppURL()` (`app/src/lib/parse-app-url.ts:98-124`) extracts the `url` field for the `open-repository-from-url` action directly from the deep-link path with no scheme check — only `branch`, `pr`, and `filepath` are validated (`testForInvalidChars`, absolute-path check). [1](#0-0) 
- That `url` flows through `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository(url)` → `CloneRepository` dialog with `initialURL: url` (`app/src/ui/dispatcher/dispatcher.ts:2215-2233`). [2](#0-1) 
- In `clone-repository.tsx`, `resolveCloneInfo()` only resolves a "safe" API-backed clone URL when `parseRemote()`/`parseRepositoryIdentifier()` can match the URL to a recognized `owner/name` GitHub pattern; any URL that doesn't match those regexes (e.g. `ext::sh -c "..."`) is returned unchanged as `{ url }`. [3](#0-2) 
- `clone()` (`app/src/lib/git/clone.ts:68-126`) only screens the destination **path** via `isClonePathSensitive`, never the URL's scheme, then does `args.push('--', url, path)` and executes `git` with that argument list. [4](#0-3) 
- Separately, `addRemote()` (`app/src/lib/git/remote.ts:28-37`) runs `git(['remote', 'add', name, url], ...)` with no scheme validation, and it is called with `pull_request.head.repo.clone_url` — a field sourced from a (possibly Enterprise/self-hosted or MITM-compromised) GitHub API server — inside `_findPullRequestBranch`/`_checkoutPullRequest` (`app/src/lib/stores/app-store.ts:8613-8721`, `app/src/ui/dispatcher/dispatcher.ts:1998-2048`). [5](#0-4) [6](#0-5) 
- `envForRemoteOperation`/`envForProxy` (`app/src/lib/git/environment.ts:76-139`) only handle proxy resolution for `http(s)` URLs and never set `GIT_ALLOW_PROTOCOL` or reject non-network protocols, so no defense-in-depth exists at the process-invocation layer. [7](#0-6) 

This mirrors the report's broken invariant: a value that should be constrained to a trusted, narrow domain (a network remote URL, analogous to the trusted `msg.sender`) is instead accepted verbatim from an untrusted source (a clicked link / API JSON field, analogous to the attacker-influenced Uniswap pool state) and used to drive a sensitive operation (git process invocation) without the missing guard (protocol allow-list, analogous to the missing slippage check).

### Impact Explanation
If a user clicks a maliciously crafted `x-github-client://openRepo/ext::sh%20-c%20"..."` link (or the Windows CLI equivalent `--protocol-launcher x-github-client://...`) and proceeds through the Clone dialog, or if a compromised/malicious GitHub Enterprise endpoint returns a poisoned `clone_url` for a fork's PR head repository that the user then checks out from Desktop, Desktop will invoke `git` with that string as the remote/clone URL. Depending on the local git version's default `protocol.ext.allow`/`protocol.file.allow` configuration, this can result in arbitrary command execution under the user's account — a full renderer/host code-execution escape from what should be a benign "open/clone a repository" action, well beyond git-object corruption alone.

### Likelihood Explanation
Exploitation requires the user to click an "Open in Desktop" style link and confirm cloning, or requires checking out a PR whose head repository originates from an untrusted/compromised API endpoint — no local access, malware, or leaked credentials needed, matching the valid-impact criteria (attacker controls a link/deep link or a GitHub API object). The likelihood is tempered by the fact that modern Git (≥2.x) defaults deny `ext`/`file` transports for "user" invoked remotes only in specific contexts (submodule recursion), while direct `git clone <ext::...>` / `git remote add` + `git fetch` invoked explicitly by the user's own git binary is often still permitted unless the local git config restricts it — actual exploitability is git-version/config dependent and was not verified against the bundled git binary in this repo, which is a limitation of this analysis performed from source code alone.

### Recommendation
Add an explicit protocol allow-list at the Desktop application layer, independent of the local git binary's defaults:
- Reject/parse-fail any URL in `parseAppURL()` (`open-repository-from-url` action) and in `clone()`/`addRemote()`/`setRemoteURL()` whose scheme is not one of `https:`, `http:`, `ssh:`, `git:`, or a bare `user@host:path` SCP-like form.
- Set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or invoke with `-c protocol.allow=never -c protocol.https.allow=always -c protocol.ssh.allow=always -c protocol.git.allow=always`) on every `git` invocation performed via `envForRemoteOperation`, so even a bypass of the application-level check cannot reach `ext::`/`fd::`/`file::` remote helpers.
- Apply the same validation to `pull_request.head.repo.clone_url`/`base.repo.clone_url` before they are passed to `addRemote()`.

### Proof of Concept
1. Attacker crafts and gets a victim to click: `x-github-client://openRepo/ext::sh -c "touch /tmp/pwned"?branch=main` (or a Windows-quoted variant delivered via `%1` protocol-launcher argument).
2. `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'ext::sh -c "touch /tmp/pwned"', branch: 'main', ... }` since only `branch`/`filepath`/`pr` are sanitized, not `url`.
3. `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository` opens the Clone dialog pre-filled with this URL; `resolveCloneInfo()` cannot match it against `parseRemote`'s regexes, so it is passed through unchanged.
4. User clicks "Clone" (the app's expected next step for this flow) → `clone(url, path, options)` in `app/src/lib/git/clone.ts` executes `git ... clone --recursive -- 'ext::sh -c "touch /tmp/pwned"' <path>`, and depending on the local git's `protocol.ext.allow` setting, the embedded shell command executes.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L732-753)
```typescript
  private async resolveCloneInfo(): Promise<IAPIRepositoryCloneInfo | null> {
    const { url, lastParsedIdentifier } = this.getSelectedTabState()

    if (url.endsWith('.wiki.git')) {
      return { url }
    }

    const account = await findAccountForRemoteURL(url, this.props.accounts)
    if (lastParsedIdentifier !== null && account !== null) {
      const api = API.fromAccount(account)
      const { owner, name } = lastParsedIdentifier
      // Respect the user's preference if they provided an SSH URL
      const protocol = parseRemote(url)?.protocol

      return api.fetchRepositoryCloneInfo(owner, name, protocol).catch(err => {
        log.error(`Failed to look up repository clone info for '${url}'`, err)
        return { url }
      })
    }

    return { url }
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

**File:** app/src/lib/stores/app-store.ts (L8640-8660)
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
