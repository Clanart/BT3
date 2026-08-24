## Verdict: Valid vulnerability

### Title
Unvalidated Generic-tab clone URL allows `ext::` remote-helper command execution via `dispatcher.clone` - (File: `app/src/ui/clone-repository/clone-repository.tsx`)

### Summary
The Generic ("URL") clone tab accepts any string as a repository URL and never validates that it uses an allowed transport (`http(s)://`, `ssh://`, `git@`). A string such as `ext::sh -c calc` is passed unchanged all the way to `git clone`, where git's built-in `ext::` remote helper interprets the remainder of the string as a shell command to execute.

### Finding Description
`updateUrl` in `clone-repository.tsx` only uses `parseRepositoryIdentifier` to derive a folder name for display; when it returns `null` (as it does for `ext::sh -c calc`, since it matches none of the `remoteRegexes` in `remote-parsing.ts`) the raw URL is stored unmodified in `urlTabState.url`: [1](#0-0) 

`checkIfCloningDisabled` — the only gate on the Clone button — checks nothing but string length, path presence, and an unrelated path-validation `error`; it performs no scheme/protocol check on `url`: [2](#0-1) 

`resolveCloneInfo` falls through to `{ url }` unchanged when `findAccountForRemoteURL`/`lastParsedIdentifier` don't match (which is the case for a non-GitHub, non-matching URL): [3](#0-2) 

`clone()`/`cloneImpl()` then hand the untouched string straight to the dispatcher: [4](#0-3) 

`Dispatcher.clone` → `AppStore._clone` → `CloningRepositoriesStore.clone` → `git/clone.ts`'s `clone()` function, which builds the final argv with `--` separating options from positional args (preventing flag injection) but doing nothing about the URL's *content*: [5](#0-4) [6](#0-5) [7](#0-6) 

The `--` before `url` blocks *option injection*, but it cannot block git's own interpretation of the value: git recognizes any argument beginning with `ext::` as a request to invoke its `ext` remote transport helper, which spawns the remainder of the string via a shell. No allowlist of protocols (e.g. `GIT_ALLOW_PROTOCOL`, `protocol.ext.allow=never`, or an application-level scheme check) is applied anywhere along this path in `app/src/lib/git/environment.ts` or `clone.ts`. Notably, `clone.ts` even explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the spawn environment: [8](#0-7) 
This is not a documented/real Git environment variable, and no other code in the indexed codebase reads or enforces it, so whatever protection it might imply is not actually wired up anywhere I could find.

I was unable to find any protocol allowlist logic (e.g., `GIT_ALLOW_PROTOCOL`, `GIT_PROTOCOL_FROM_USER=0`, or `protocol.*.allow`) set for the clone/fetch environment, and no scheme check exists in the UI layer (`checkIfCloningDisabled`, `updateUrl`, `resolveCloneInfo`). `enterprise-validate-url.ts` demonstrates the pattern used elsewhere in the app (rejecting non-`https:` protocols) but this pattern is not applied to the clone URL field.

### Impact Explanation
If exploited, this results in arbitrary command execution on the victim's machine at the moment they click "Clone", using whatever privileges the Desktop app / user has. This satisfies the "code execution" impact class explicitly listed as valid in the review scope, reached via a link/deep link or manually pasted URL that an attacker can induce a user to enter or click (e.g. via `x-github-client://openRepo/…` or a crafted "Open in Desktop" deep link that pre-fills `initialURL`, which flows into the same `updateUrl`/`clone` path via `openOrCloneRepository`).

### Likelihood Explanation
Requires the victim to type/paste or be redirected (via a crafted deep link) to a malicious URL into the Generic clone tab and click "Clone" — a single, plausible user action, not requiring special local access or privileges, consistent with "unprivileged attacker-controlled... clicked links/deep links" in scope.

### Recommendation
Enforce an allowlist of transport schemes (`https:`, `http:`, `ssh:`, `git:`, and the `git@host:` SCP-like form) before ever passing a URL to `dispatcher.clone`/`git clone`, rejecting anything else (including `ext::`, `fd::`, `file://`) both in `checkIfCloningDisabled`/`updateUrl` (UI-level early rejection) and defensively in `app/src/lib/git/clone.ts` (defense-in-depth), and/or explicitly set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or `protocol.ext.allow=never`, `protocol.file.allow=never`) in the environment used for clone/fetch/push operations.

### Proof of Concept
1. Open GitHub Desktop → File → Clone Repository → URL tab.
2. Enter `ext::sh -c calc` (or `ext::sh -c "touch /tmp/pwned"` on macOS/Linux) as the URL, pick any empty destination folder.
3. Click "Clone". `checkIfCloningDisabled` returns `false` (non-empty URL, valid empty path, no error), so the button is enabled.
4. `cloneImpl` → `dispatcher.clone('ext::sh -c calc', path, {...})` → `git/clone.ts` executes `git ... clone --recursive -- ext::sh -c calc <path>`, and git's `ext` remote helper executes `sh -c calc`, running attacker-chosen shell commands.

A regression test could assert that `CloneRepository`'s `clone()`/`cloneImpl()` reject or sanitize a URL string beginning with `ext::` (or any scheme outside an allowlist) before calling `dispatcher.clone`.

### Citations

**File:** app/src/ui/clone-repository/clone-repository.tsx (L301-314)
```typescript
  private checkIfCloningDisabled = () => {
    const tabState = this.getSelectedTabState()
    const { error, url, path } = tabState
    const { loading } = this.state

    const disabled =
      url.length === 0 ||
      path == null ||
      path.length === 0 ||
      loading ||
      error !== null

    return disabled
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L763-802)
```typescript
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

    setDefaultDir(Path.resolve(path, '..'))
  }
```

**File:** app/src/lib/git/clone.ts (L81-84)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
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

**File:** app/src/lib/stores/cloning-repositories-store.ts (L20-38)
```typescript
  public async clone(
    url: string,
    path: string,
    options: CloneOptions
  ): Promise<boolean> {
    const repository = new CloningRepository(path, url)
    this._repositories.push(repository)

    const title = `Cloning into ${path}`

    this.stateByID.set(repository.id, { kind: 'clone', title, value: 0 })
    this.emitUpdate()

    let success = true
    try {
      await cloneRepo(url, path, options, progress => {
        this.stateByID.set(repository.id, progress)
        this.emitUpdate()
      })
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L832-845)
```typescript
  /** Clone the repository to the path. */
  public async clone(
    url: string,
    path: string,
    options?: { branch?: string; defaultBranch?: string }
  ): Promise<Repository | null> {
    return this.appStore._completeOpenInDesktop(async () => {
      const { promise, repository } = this.appStore._clone(url, path, options)
      await this.selectRepository(repository)
      const success = await promise
      // TODO: this exit condition is not great, bob
      if (!success) {
        return null
      }
```
