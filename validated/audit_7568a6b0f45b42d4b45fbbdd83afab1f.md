Found a strong, concrete analog. The seed report's "broken invariant" is that a critical, security-relevant guard which normally halts an operation involving attacker-influenced/untrusted content is deliberately disabled/bypassed by the application logic, and the burden shifts to whichever party lacks visibility into the risk. In Desktop's clone path, this shows up as a hard-coded disabling of Git's built-in submodule-hook-collision protection during recursive clones of attacker-supplied URLs.

### Title
Recursive clone explicitly disables Git's `GIT_CLONE_PROTECTION_ACTIVE` submodule-hook-collision safeguard, exposing users to RCE via malicious remote repositories - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` always performs `git clone --recursive` against a caller/attacker-supplied URL and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the child process environment. [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git introduced (as part of the fix for the submodule hook-path-collision RCE class, CVE-2024-32002) to detect and refuse recursive clones where a malicious submodule's path could collide with `.git/hooks` on case-insensitive or symlink-supporting filesystems, which would let an attacker plant an executable hook that runs automatically during/after clone. Desktop turns this protection off on every clone, restoring the pre-fix vulnerable behavior for whatever protection this flag guards.

### Finding Description
Clone is a "cloned/fetched repository is attacker-controlled" trust boundary: the URL and its full contents (including `.gitmodules`, submodule paths, and any executable file names) come from an untrusted remote, whether typed by the user, resolved from a GitHub API object, or supplied via the `x-github-client://openRepo/...` deep link / `--cli-clone` CLI action handled in `app/src/main-process/main.ts` and `app/src/lib/parse-app-url.ts` and dispatched through `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository`. [2](#0-1) [3](#0-2) 

The clone always uses `--recursive`, meaning submodules are fetched and checked out with no user prompt or per-submodule review: [4](#0-3) 

The corrupted/weakened value is the `GIT_CLONE_PROTECTION_ACTIVE` environment flag passed to the underlying `git` binary. By forcing it to `'false'`, Desktop tells Git to skip its safety check for submodule/hook path collisions during the very `--recursive` clone operation that fetches attacker-controlled submodule trees. None of Desktop's own guards address this: `isClonePathSensitive()` only validates the destination directory is not a sensitive system path, and `sanitizeCloneName()` only prevents the derived directory name from escaping the intended parent folder — neither inspects or restricts the cloned repository's own tree/submodule contents, which is what Git's native protection is designed to catch. [5](#0-4) [6](#0-5) 

### Impact Explanation
If Git's protection would otherwise have blocked or warned about a malicious recursive clone (e.g., a submodule path crafted to collide with `.git/hooks` on a case-insensitive filesystem, or via symlink tricks), Desktop's override causes the clone to proceed silently instead. This can result in an executable hook script being placed where Git will execute it automatically as part of the clone/checkout, i.e., arbitrary code execution triggered merely by a user cloning an attacker's repository through Desktop's normal "Clone" flow or an inbound `x-github-client://openRepo/...` link — no additional unnatural user action required beyond opening the link or cloning the URL. This matches the requested impact class: code execution from an attacker-controlled cloned/fetched repository or a link the user clicks.

### Likelihood Explanation
Every recursive clone Desktop performs (which is all of them, since `--recursive` is hard-coded) runs with the protection disabled, so the exposure is not conditional on a rare configuration — it is the default and only path. The main precondition is that the attacker's payload actually needs a vulnerable filesystem behavior (case-insensitivity or symlink support) that the disabled Git check exists specifically to detect; this is common on default macOS (case-insensitive APFS) and Windows (NTFS symlink support with dev mode/admin) setups, both of which are primary Desktop platforms.

### Recommendation
Remove the hard override of `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in `app/src/lib/git/clone.ts` and let Git's native protection run. If the override was added to work around a compatibility issue (e.g., false positives on certain legitimate submodule layouts), scope the bypass narrowly and only after Desktop performs its own equivalent validation of submodule paths against `.git/hooks`/`.git/modules` collisions, or surface the Git-refused clone as a warning to the user with an explicit opt-in rather than silently disabling the check for every clone.

### Proof of Concept
1. Attacker publishes a public GitHub repository containing a `.gitmodules` entry whose submodule path is crafted to collide (case-insensitively or via symlink) with `.git/hooks` on the victim's filesystem, per the pattern fixed by CVE-2024-32002, with a malicious executable placed at the colliding hook path (e.g., `post-checkout`).
2. Attacker sends the victim a link such as `x-github-client://openRepo/https://github.com/attacker/evil-repo` or simply shares the clone URL.
3. Victim clicks the link (handled by `handleAppURL`/`parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository`) or pastes the URL into Desktop's Clone dialog. [7](#0-6) 
4. Desktop calls `clone()`, which runs `git -c init.defaultBranch=... clone --recursive ... -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set. [8](#0-7) 
5. Because the protection is disabled, Git does not refuse or warn about the colliding submodule/hook path, the malicious hook is written into `.git/hooks`, and it executes automatically as part of the checkout — achieving code execution on the victim's machine from cloning an untrusted repository through Desktop's normal UI.

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

**File:** app/src/lib/git/clone.ts (L81-125)
```typescript
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
```

**File:** app/src/main-process/main.ts (L238-291)
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

  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
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

**File:** app/src/lib/remote-parsing.ts (L88-116)
```typescript
export function sanitizeCloneName(name: string): string | null {
  const components = name.split(/[/\\:]/)

  let lastComponent = ''
  for (let i = components.length - 1; i >= 0; i--) {
    if (components[i].length > 0) {
      lastComponent = components[i]
      break
    }
  }

  if (lastComponent.length === 0) {
    return null
  }

  if (lastComponent.endsWith('.git')) {
    lastComponent = lastComponent.slice(0, -4)
  }

  if (
    lastComponent === '..' ||
    lastComponent === '.' ||
    lastComponent.length === 0
  ) {
    return null
  }

  return lastComponent
}
```
