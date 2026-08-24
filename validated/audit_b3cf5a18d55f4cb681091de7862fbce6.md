## Analysis

The Diligence report's core pattern is: **an untrusted, attacker-influenceable value bypasses an implicit trust boundary because the code path that consumes it never validates the value's *shape/scheme* before using it in a security-sensitive operation** (here, an unvalidated block hash silently becomes a second "valid" randomness input).

The closest real analog in this codebase is in the "Open repository from URL" deep-link flow, where the `url` component of an `x-github-client://openrepo/...` link is never validated against an allow-list of expected git transport forms (`https://`, `git@host:...`, `ssh://`, etc.) before it is threaded straight into a `git clone` invocation.

### Title
Unvalidated deep-link repository URL reaches `git clone`, enabling `ext::`/`fd::` transport command execution - (File: app/src/lib/parse-app-url.ts, app/src/lib/git/clone.ts)

### Summary
`parseAppURL` extracts the `url` for the `open-repository-from-url` action straight from the deep-link path with no scheme/format validation — only the `branch` query parameter is sanitized. [1](#0-0)  That raw string flows unchanged through `Dispatcher.openOrCloneRepository`, which pre-fills the Clone Repository dialog with it, [2](#0-1)  and ultimately into `git/clone.ts`'s `clone()` function, which appends the URL directly as an argument to `git clone`. [3](#0-2)  Because git supports the `ext::`/`fd::` remote-helper transports (which execute an arbitrary shell command as the "remote"), a crafted deep link can pre-populate the clone field with such a URL, and the click of the existing "Clone" button executes attacker-chosen code.

### Finding Description
`parseAppURL` bails out only on missing hostname/pathname and validates `pr`/`branch` with regexes via `testForInvalidChars`, but the repository `url` itself — extracted from the raw pathname — is returned as-is: [4](#0-3) 

This action is dispatched to `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository`, which, if no existing repository matches, opens the Clone dialog with `initialURL: url` — the attacker-controlled string, unmodified: [2](#0-1) 

In `clone-repository.tsx`, `resolveCloneInfo()` only special-cases `.wiki.git` URLs and otherwise returns `{ url }` verbatim if it cannot resolve a GitHub account/API clone info for it — there is no scheme allow-list check anywhere in this path: [5](#0-4) 

That value is passed to `dispatcher.clone(url, path, ...)` → `AppStore._clone` → `CloningRepositoriesStore.clone` → `git/clone.ts`'s `clone()`, which builds the git command line as `['clone', '--recursive', ..., '--', url, path]`: [6](#0-5) 

The `--` separator (added, per the `remote-parsing.ts` comments, as a defense against argument-injection style attacks like CVE-2017-1000117) prevents `url` from being interpreted as a leading command-line flag, but it does **not** stop git from interpreting a value like `ext::sh -c "some-command" --` or `fd::N` as a *transport specification*. Git's `ext::` remote helper spawns an arbitrary shell command in place of a real transport, and `fd::` operates on arbitrary file descriptors — both are recognized as valid remote URL schemes by `git clone`, independent of the `--` separator, when the URL is supplied as a direct, user-invoked clone target (this codebase does not restrict `protocol.ext.allow`/`protocol.fd.allow` before invoking `git`).

The corrupted value here is the `url` field of `IOpenRepositoryFromURLAction`: the invariant "repository URLs handled by Desktop are limited to git's ssh/https/git transports" is silently broken because no code on this path enforces it — exactly analogous to the Entropy report's failure to reject a block hash value ("zero") that should have been treated as invalid before being trusted.

### Impact Explanation
If exploited, this allows arbitrary command execution on the victim's machine as soon as the pre-filled "Clone" action is confirmed, driven entirely by content the attacker controls in a link the victim clicked (an `x-github-client://openrepo/...` deep link, or an "Open in Desktop" button on a malicious/compromised web page). This matches the "attacker controls...a link or deep link the user clicks" → "code execution" criteria explicitly listed as valid impact.

### Likelihood Explanation
No existing guard in `parse-app-url.ts`, `dispatcher.ts`, `clone-repository.tsx`, or `git/clone.ts` performs scheme/protocol allow-listing on the clone URL before it reaches `git clone`. The only sanity checks present (`isClonePathSensitive` in `clone.ts`) validate the *destination path*, not the *source URL scheme*. [7](#0-6)  The user does need to click "Clone" in the pre-filled dialog, but this is the ordinary, expected flow for an "Open in Desktop"/"Clone" deep link and does not require any unnatural steps.

### Recommendation
- **Short term**: In `parseAppURL`, validate that the extracted `url` for `open-repository-from-url` matches an expected git remote form (as already implemented by `parseRemote` in `remote-parsing.ts`) or begins with an allow-listed scheme (`https://`, `git@`, `ssh://`, `git://`); reject (return `unknown`) otherwise. Additionally, harden `git/clone.ts`/`envForRemoteOperation` to explicitly set `protocol.ext.allow=never` and `protocol.fd.allow=never` (and disable other non-network transports) for all git invocations originating from clone/fetch flows driven by external input.
- **Long term**: Audit every place a URL-like string sourced from deep links, GitHub API objects, or remote configuration reaches a `git` invocation, and centralize protocol allow-listing in one shared validation utility used consistently across `clone.ts`, `remote-parsing.ts`, and the trampoline/credential-helper code.

### Proof of Concept
1. Attacker crafts: `x-github-client://openrepo/ext::sh%20-c%20%22touch%20/tmp/pwned%22`
   (or, depending on OS URL decoding behavior, an unencoded variant with an `ext::` payload).
2. Victim, with GitHub Desktop installed and registered as the `x-github-client` protocol handler, [8](#0-7)  clicks the link.
3. `handleAppURL` → `parseAppURL` accepts the URL, returning `{ name: 'open-repository-from-url', url: 'ext::sh -c "touch /tmp/pwned"' }` since no scheme check exists. [9](#0-8) 
4. `Dispatcher.openOrCloneRepository` finds no matching existing repository and opens the Clone dialog pre-filled with this "URL". [10](#0-9) 
5. Victim clicks "Clone" (the natural, expected action). `clone()` in `git/clone.ts` runs `git ... clone --recursive -- 'ext::sh -c "touch /tmp/pwned"' <path>`, and git's `ext::` helper executes the attacker's command. [3](#0-2) 

**Uncertainty**: I could not execute the code to confirm (a) exactly how Node's `URL.parse` / the OS deep-link dispatch handles percent-encoding of the malicious payload end-to-end, or (b) whether git's `protocol.ext.allow` default in the bundled dugite/git build treats this as "user"-context (allowed) versus blocked. These are runtime/environment-dependent details that would need to be validated in a live Desktop build; a Devin session with a running build and OS-level protocol registration would be needed to fully confirm exploitability versus git's own `ext::` allow-list defaults.

### Citations

**File:** app/src/lib/parse-app-url.ts (L90-124)
```typescript
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L88-126)
```typescript
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
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

**File:** app/src/main-process/main.ts (L102-116)
```typescript
/** Extra argument for the protocol launcher on Windows */
const protocolLauncherArg = '--protocol-launcher'

const possibleProtocols = new Set(['x-github-client'])
if (__DEV_SECRETS__) {
  possibleProtocols.add('x-github-desktop-dev-auth')
} else {
  possibleProtocols.add('x-github-desktop-auth')
}
// Also support Desktop Classic's protocols.
if (__DARWIN__) {
  possibleProtocols.add('github-mac')
} else if (__WIN32__) {
  possibleProtocols.add('github-windows')
}
```
