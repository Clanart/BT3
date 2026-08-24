## Analysis

The report's core pattern is: **a privileged/free-resource automation path executes on attacker-chosen input (arbitrary token/position-manager address) without the safety checks a careful implementation would need**, letting an outside party redirect trusted execution toward a malicious target. The closest verifiable analog in this codebase is **GitHub Desktop's `clone()` implementation explicitly disabling Git's own anti-hook-execution safeguard for every clone, including clones of arbitrary/attacker-supplied remote URLs.**

### Title
Git clone protection (`GIT_CLONE_PROTECTION_ACTIVE`) is unconditionally disabled for all clones, re-enabling hostile-repository hook execution - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every invocation of `git clone --recursive`, regardless of where the URL came from (Clone dialog, `x-github-client://openRepo/...` deep links, or the `github clone <url>` CLI). This environment variable is the guard Git itself introduced to stop a hostile repository from executing arbitrary hook code during a (recursive/submodule) clone before the user has any chance to inspect the content. Desktop turns this protection off for the entire application, meaning any attacker who gets a victim to clone or "Open in Desktop" a crafted repository can potentially trigger hook execution during the clone itself. [1](#0-0) 

### Finding Description
`clone()` builds the execution environment as:

```ts
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = ['-c', `init.defaultBranch=${defaultBranch}`, 'clone', '--recursive']
...
args.push('--', url, path)
await git(args, __dirname, 'clone', opts)
``` [2](#0-1) 

This runs on *every* clone path in the app:
- The Clone Repository dialog, where `cloneImpl` passes a user-typed URL straight to `dispatcher.clone` → `_clone` → `cloningRepositoriesStore.clone`. [3](#0-2) [4](#0-3) 
- The `x-github-client://openRepo/<url>` protocol handler, which is invoked by clicking an "Open in Desktop" link on any web page and flows into `openOrCloneRepository(url)` with no additional trust check on the target URL. [5](#0-4) [6](#0-5) 
- The `github clone <url>` CLI entry point, which similarly forwards an arbitrary URL argument. [7](#0-6) 

The only guard present, `isClonePathSensitive`, checks the **destination path** on disk (home dir, `.ssh`, `.gnupg`, etc.) — it never inspects the content of the remote being cloned, so it cannot mitigate a hostile repository payload. [8](#0-7) 

Desktop's separate "unsafe repository" / ownership check (`addSafeDirectory`, `getRepositoryType`) only fires for a directory that already exists on disk and is owned by a different OS user — it is a post-hoc UI warning shown when *adding an existing local folder*, and does not run during, or before, `clone()`'s own process invocation. Because Desktop creates the destination directory itself as the current user, this check can never engage for a freshly attacker-hosted repository being cloned through Desktop, so it provides no protection for this path. [9](#0-8) [10](#0-9) 

Since `--recursive` is always passed, submodules are also cloned/checked out with the same disabled protection, which is precisely the scenario the upstream Git protection variable was designed to cover (nested/submodule clone hook execution from repository-controlled content). [11](#0-10) 

### Impact Explanation
An attacker who controls a git repository (hosted anywhere, not necessarily on github.com, since the Clone dialog and CLI accept arbitrary generic URLs) can craft it to exploit the hook-execution class of bug that `GIT_CLONE_PROTECTION_ACTIVE` exists to stop. Because Desktop forces this protection off for every clone it performs, a victim who:
1. Pastes/enters the attacker's URL into "Clone Repository" → "URL" tab, or
2. Clicks an "Open in Desktop"-style deep link (`x-github-client://openRepo/<attacker-url>`), or
3. Runs `github clone <attacker-url>` from the CLI,

may have attacker-controlled code executed as a git hook on their machine during the clone, before they ever open, inspect, or explicitly trust the resulting repository. This satisfies the "attacker controls a cloned/fetched repository ... result is code execution" impact category directly.

### Likelihood Explanation
The attacker primitive requires only that the victim clone or open a link to a repository the attacker controls — a normal, expected Desktop workflow with no local access, admin rights, or pre-existing compromise needed. The disabling of the protection is unconditional and applies to 100% of clone operations in the app (dialog, CLI, and deep link), so there is no code path that reaches `clone()` with the safeguard intact. The remaining uncertainty is whether the currently bundled/embedded Git version still contains an exploitable hook-execution bug that this variable protects against; that depends on the exact `dugite`/embedded Git version, which was not directly inspected here, but Desktop is explicitly and deliberately opting out of a security control that Git upstream ships in the default "on" state for exactly this scenario.

### Recommendation
- Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()`, or scope it only to internal/trusted recursive submodule sub-clones that Desktop itself controls and has already validated, not to top-level user- or link-supplied URLs.
- If the override exists to suppress duplicate warnings/noise during `--recursive` clones, replace it with explicit validation of submodule URLs/paths (e.g., reject `file://`/relative-parent submodule paths, validate hook directories are not symlinks) rather than disabling the entire Git protection mechanism.
- Ensure the embedded/bundled Git version used via `dugite` is kept current with upstream security fixes for clone/hook execution.

### Proof of Concept
1. Attacker publishes a git repository (`https://attacker.example/evil.git`) crafted to trigger the hook-execution behavior that `GIT_CLONE_PROTECTION_ACTIVE` is designed to prevent during a recursive clone (e.g., a submodule/hooks-path structure of the kind the corresponding upstream Git protection blocks).
2. Attacker sends the victim a link: `x-github-client://openRepo/https://attacker.example/evil.git` or simply the clone URL to paste into Desktop's Clone dialog.
3. Victim clicks the link (handled by `main.ts` → `handleAppURL` → `dispatcher.openOrCloneRepository`) or pastes the URL into the Clone Repository dialog and clicks Clone.
4. Desktop calls `clone(url, path, options)`, which sets `GIT_CLONE_PROTECTION_ACTIVE=false` and runs `git clone --recursive -- <url> <path>`.
5. Because Git's built-in protection is disabled, the crafted repository's hook payload executes on the victim's machine during the clone, before the user ever opens the repository in Desktop. [12](#0-11) [5](#0-4)

### Citations

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L797-802)
```typescript
  private cloneImpl(url: string, path: string, defaultBranch?: string) {
    this.props.dispatcher.clone(url, path, { defaultBranch })
    this.props.onDismissed()

    setDefaultDir(Path.resolve(path, '..'))
  }
```

**File:** app/src/lib/stores/app-store.ts (L5670-5690)
```typescript
  public _clone(
    url: string,
    path: string,
    options: { branch?: string; defaultBranch?: string } = {}
  ): {
    promise: Promise<boolean>
    repository: CloningRepository
  } {
    const promise = this.cloningRepositoriesStore.clone(url, path, options)
    const repository = this.cloningRepositoriesStore.repositories.find(
      r => r.url === url && r.path === path
    )!

    promise.then(success => {
      if (success) {
        this.statsStore.recordCloneRepository()
      }
    })

    return { promise, repository }
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

**File:** app/src/main-process/main.ts (L238-280)
```typescript
async function handleCommandLineArguments(argv: string[]) {
  const args = parseCommandLineArgs(argv, {
    boolean: ['protocol-launcher'],
  })

  // Desktop registers it's protocol handler callback on Windows as
  // `[executable path] --protocol-launcher "%1"`. Note that extra command
  // line arguments might be added by Chromium
  // (https://electronjs.org/docs/api/app#event-second-instance).

  if (__WIN32__ && args['protocol-launcher'] === true) {
    // On Windows we'll end up getting called with something like
    // `--protocol-launcher --allow-file-access-from-files x-github-client://..`
    // which minimist naturally interprets as
    // `--allow-file-access-from-files=x:/github-client`. This is due to
    // Chromium's hot take on parsing command line arguments, see:
    // https://github.com/electron/electron/issues/20322#issuecomment-534137321
    // So while we could add '--allow-file...' as a boolean we can't know for
    // sure that Chromium won't add more switches later on which is why we have
    // to resort to looking through all arguments looking for something that
    // appears to be an app url.
    const prefixes = Array.from(possibleProtocols, p => `${p}://`)
    const matchingUrl = argv.find(arg => {
      if (prefixes.some(p => arg.startsWith(p))) {
        try {
          new URL(arg)
          return true
        } catch (e) {
          log.error(`Unable to parse argument as URL: ${arg}`)
        }
      }
      return false
    })

    if (matchingUrl) {
      handleAppURL(matchingUrl)
    } else {
      log.error(`Encountered --protocol-launcher without app url`)
    }
    // If --protocol-launcher is present we always want to bail and not
    // risk a smuggled cli switch
    return
  }
```

**File:** app/src/cli/main.ts (L55-69)
```typescript
} else if (args._.at(0) === 'clone') {
  const urlArg = args._.at(1)
  // Assume name with owner slug if it looks like it
  const url =
    urlArg && /^[^\/]+\/[^\/]+$/.test(urlArg)
      ? `https://github.com/${urlArg}`
      : urlArg

  if (!url) {
    usage(1)
  } else if (typeof args.branch === 'string') {
    run(`--cli-clone=${url}`, `--cli-branch=${args.branch}`)
  } else {
    run(`--cli-clone=${url}`)
  }
```

**File:** app/src/ui/missing-repository.tsx (L129-150)
```typescript
          </div>

          <Row>{buttons}</Row>
        </UiView>
      )
    }

    return (
      <UiView id="missing-repository-view">
        <div className="title-container">
          <div className="title">Can't find "{this.props.repository.name}"</div>
          <div className="details">
            It was last seen at{' '}
            <span className="path">{this.props.repository.path}</span>.{' '}
            <LinkButton onClick={this.checkAgain}>Check&nbsp;again.</LinkButton>
          </div>
        </div>

        <Row>{buttons}</Row>
      </UiView>
    )
  }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-174)
```typescript
  private buildRepositoryUnsafeError() {
    const { repositoryUnsafePath, path } = this.state
    if (
      !this.state.path.length ||
      !this.state.showNonGitRepositoryWarning ||
      !this.state.isRepositoryUnsafe ||
      repositoryUnsafePath === undefined
    ) {
      return null
    }

    // Git for Windows will replace backslashes with slashes in the error
    // message so we'll do the same to not show "the repo at path c:/repo"
    // when the entered path is `c:\repo`.
    const convertedPath = __WIN32__ ? path.replaceAll('\\', '/') : path

    const displayedMessage = (
      <>
        <p>
          The Git repository
          {repositoryUnsafePath !== convertedPath && (
            <>
              {' at '}
              <Ref>{repositoryUnsafePath}</Ref>
            </>
          )}{' '}
          appears to be owned by another user on your machine. Adding untrusted
          repositories may automatically execute files in the repository.
        </p>
        <p>
          If you trust the owner of the directory you can
          <LinkButton onClick={this.onTrustDirectory}>
            {' '}
            add an exception for this directory
          </LinkButton>{' '}
          in order to continue.
        </p>
      </>
    )

    const screenReaderMessage = `The Git repository appears to be owned by another user on your machine.
      Adding untrusted repositories may automatically execute files in the repository.
      If you trust the owner of the directory you can add an exception for this directory in order to continue.`

    return { screenReaderMessage, displayedMessage }
  }
```
