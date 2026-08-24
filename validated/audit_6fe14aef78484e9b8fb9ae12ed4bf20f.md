Confirmed: no protocol allowlist (`GIT_ALLOW_PROTOCOL` or similar) exists anywhere in the codebase, and the URL flows unfiltered from the deep-link parser through the Clone dialog straight into `git clone` argument. This is a genuine, unpatched vector.

### Title
Deep-link "Open Repository" URL is passed unvalidated into `git clone`, enabling arbitrary command execution via Git's `ext::` transport - ([File: app/src/lib/git/clone.ts])

### Summary
GitHub Desktop registers itself as the handler for `x-github-client://` (and legacy `github-mac`/`github-windows`) protocol links. The `openRepo` action extracts a fully attacker-controlled `url` string from the link and feeds it, without any scheme allow-listing, into the "Clone repository" flow, which ultimately executes `git clone -- <url> <path>`. Because Git supports the `ext::<command>` remote helper transport (and Desktop never restricts allowed protocols via `GIT_ALLOW_PROTOCOL` or `-c protocol.ext.allow=...`), a crafted deep link containing `ext::sh -c ...` results in arbitrary command execution on the victim's machine once they confirm the clone.

### Finding Description
The URL handling chain is:
1. `parseAppURL` extracts the raw `url` path segment from an `openRepo` deep link with no scheme/format validation beyond checking `branch`/`pr` query params: [1](#0-0) 
2. `main.ts` routes the parsed action straight to the renderer via `handleAppURL`/`sendURLAction`: [2](#0-1) 
3. `Dispatcher.openRepositoryFromUrl` calls `openOrCloneRepository(url)` when no `pr`/`branch` is present, which opens the Clone dialog pre-filled with the raw URL — with **no scheme check**: [3](#0-2) 
4. `CloneRepository.updateUrl` stores the raw string as `tabState.url` regardless of whether `parseRepositoryIdentifier` can parse it as a normal GitHub-style remote (used only to derive a folder name, not to validate the URL itself): [4](#0-3) 
5. On clicking "Clone", `resolveCloneInfo` falls back to `{ url }` verbatim when the URL isn't a recognized GitHub identifier or the account lookup fails, and passes it to `cloneImpl` → `dispatcher.clone`: [5](#0-4) 
6. This reaches `lib/git/clone.ts`, which does check the **destination path** for sensitive locations, but performs **no validation of the `url` protocol** before building the Git command line. The URL is placed after `--` (blocking flag/argument injection) but that does not stop Git from interpreting a valid transport scheme such as `ext::`: [6](#0-5) 
7. `envForRemoteOperation`/`envForProxy` only handle `http(s)://` proxy resolution; there is no `GIT_ALLOW_PROTOCOL` environment variable or `-c protocol.<x>.allow` restriction set anywhere in the environment/clone code path: [7](#0-6) 

Git's built-in `ext::<command>` remote helper executes an arbitrary shell command as the "remote" transport (this is the mechanism behind CVE-2017-1000117, and Git's own submodule-recursion protections — `protocol.ext.allow=user` — assume the *top-level* clone URL is a value the user directly and knowingly typed). Desktop breaks that assumption: the value silently originates from an attacker-supplied deep link, not manual user entry, yet is passed to Git exactly as if it were manually typed, satisfying Git's "user" trust level and bypassing the very protection Git designed for this class of attack.

### Impact Explanation
A remote attacker who gets a victim to click a single `x-github-client://openRepo/ext::sh%20-c%20%22<payload>%22` link (e.g. via a webpage, chat message, or malicious "Open in Desktop" button) can achieve arbitrary command execution in the victim's user context as soon as the victim presses "Clone" in the pre-filled dialog — no repository content or prior local access is needed. This satisfies the "link/deep-link the user clicks → code execution" impact class.

### Likelihood Explanation
Likelihood is high: `x-github-client://` is registered as the default protocol handler on install [8](#0-7) , the `openRepo` action requires no authentication/account, and the "Clone" step is presented as the ordinary intended workflow after clicking an "Open in Desktop" style link, making it a single expected click rather than an unnatural user action. No existing guard (path sensitivity check, `--` separator, `sanitizeCloneName`) inspects or restricts the URL's scheme.

### Recommendation
Before passing any URL into `git clone`/`git remote add`/`git fetch` originating from an untrusted source (deep links, API responses, `.gitmodules`), validate that the scheme is restricted to an explicit allow-list (`https:`, `git:`, `ssh:`, and the `git@host:` SCP-like form) and reject everything else, or invoke Git with `GIT_ALLOW_PROTOCOL=http:https:ssh` (and `-c protocol.ext.allow=never -c protocol.file.allow=never`) for all remote operations, mirroring the mitigation Git itself ships for CVE-2017-1000117-class issues.

### Proof of Concept
1. Attacker crafts and hosts/sends: `x-github-client://openRepo/ext::sh%20-c%20%22touch%20/tmp/pwned%22`
2. Victim (with GitHub Desktop installed and registered as the protocol handler) clicks the link.
3. Desktop opens with the Clone dialog, "URL" tab, pre-filled with `ext::sh -c "touch /tmp/pwned"` (verifiable via `parseAppURL`/`updateUrl` behavior shown above).
4. Victim clicks "Clone".
5. `clone()` in `app/src/lib/git/clone.ts` executes `git -c init.defaultBranch=... clone --recursive -- "ext::sh -c \"touch /tmp/pwned\"" <path>`, and Git's `ext::` helper spawns `sh -c "touch /tmp/pwned"`, executing attacker-controlled shell command on the victim's machine.

### Citations

**File:** app/src/lib/parse-app-url.ts (L96-124)
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L732-799)
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

  private onItemClicked = (repository: IAPIRepository, source: ClickSource) => {
    if (source.kind === 'keyboard' && source.event.key === 'Enter') {
      if (this.checkIfCloningDisabled() === false) {
        this.clone()
      }
    }
  }

  private clone = async () => {
    this.setState({ loading: true })

    const cloneInfo = await this.resolveCloneInfo()
    const { path } = this.getSelectedTabState()

    if (path == null) {
      const error = new Error(`Directory could not be created at this path.`)
      this.setState({ loading: false })
      this.setSelectedTabState({ error })
      return
    }

    if (!cloneInfo) {
      const error = new Error(
        `We couldn't find that repository. Check that you are logged in, the network is accessible, and the URL or repository alias are spelled correctly.`
      )
      this.setState({ loading: false })
      this.setSelectedTabState({ error })
      return
    }

    const { url, defaultBranch } = cloneInfo

    this.props.dispatcher.closeFoldout(FoldoutType.Repository)
    try {
      this.cloneImpl(url.trim(), path, defaultBranch)
    } catch (e) {
      log.error(`CloneRepository: clone failed to complete to ${path}`, e)
      this.setState({ loading: false })
      this.setSelectedTabState({ error: e })
    }
  }

  private cloneImpl(url: string, path: string, defaultBranch?: string) {
    this.props.dispatcher.clone(url, path, { defaultBranch })
    this.props.onDismissed()
```

**File:** app/src/lib/git/clone.ts (L68-126)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

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

**File:** app/src/lib/git/environment.ts (L76-139)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}

/**
 * Not intended to be used directly. Exported only in order to
 * allow for testing.
 *
 * @param remoteUrl The remote url to resolve a proxy for.
 * @param env       The current environment variables, defaults
 *                  to `process.env`
 * @param resolve   The method to use when resolving the proxy url,
 *                  defaults to `resolveGitProxy`
 */
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

  // Note that HTTPS here doesn't mean that the proxy is HTTPS, only
  // that all requests to HTTPS protocols should be proxied. The
  // proxy protocol is defined by the url returned by `this.resolve()`
  const proto = protocolMatch[1].toLowerCase() // http or https

  // We'll play it safe and say that if the user has configured
  // the ALL_PROXY environment variable they probably know what
  // they're doing and wouldn't want us to override it with a
  // protocol-specific proxy. cURL supports both lower and upper
  // case, see:
  // https://github.com/curl/curl/blob/14916a82e/lib/url.c#L2180-L2185
  if ('ALL_PROXY' in env || 'all_proxy' in env) {
    log.info(`proxy url not resolved, ALL_PROXY already set`)
    return
  }

  // Lower case environment variables due to
  // https://ec.haxx.se/usingcurl/usingcurl-proxies#http_proxy-in-lower-case-only
  const envKey = `${proto}_proxy` // http_proxy or https_proxy

  // If the user has already configured a proxy in the environment
  // for the protocol we're not gonna override it.
  if (envKey in env || (proto === 'https' && 'HTTPS_PROXY' in env)) {
    log.info(`proxy url not resolved, ${envKey} already set`)
    return
  }

  const proxyUrl = await resolve(remoteUrl).catch(err => {
    log.error('Failed resolving Git proxy', err)
    return undefined
  })

  return proxyUrl === undefined ? undefined : { [envKey]: proxyUrl }
}
```
