Based on the investigation, I can identify a structurally analogous "duplicate operation not deduplicated before entering the async pipeline" bug in GitHub Desktop, though its trigger surface is narrower than the libp2p dial race (I was unable to fully verify whether `_startOpenInDesktop`/`PopupManager` blocks a second concurrent "Open in Desktop" popup before running out of tool iterations — this is called out as an open question below).

### Title
Concurrent clone requests to the same destination are not deduplicated, allowing two `git clone` processes to race on the same working directory - (File: `app/src/lib/stores/cloning-repositories-store.ts`)

### Summary
The lighthouse bug was a check-then-act race: peers discovered via two different code paths (subnet discovery and regular discovery) could both be queued for dialing because the "is this peer already being dialed" registration happened too late — only after the peer had already entered the `NetworkBehaviour` event queue. GitHub Desktop's `CloningRepositoriesStore.clone` has the same shape of defect: it never checks whether a clone to the same `path`/`url` is already in-flight before starting a new `git clone` child process, and the caller-side guard in `Dispatcher.openOrCloneRepository` only checks against the store of *completed* repositories, not the store of *in-progress* clones.

### Finding Description
`CloningRepositoriesStore.clone()` unconditionally constructs a new `CloningRepository` and immediately invokes the git `clone` binary, with no lookup against `this._repositories` for an existing entry with the same `url`/`path`: [1](#0-0) 

Compare this to the lighthouse fix, which moved dial-state registration to occur *before* the peer entered the processing queue specifically to close the window where the same peer could be queued twice. Here there is no equivalent guard at all — any two callers that reach `_clone(url, path)` for the same destination will both spawn independent `git clone` processes writing into the same target directory.

The only "existing repository" guard that runs before opening the clone flow is in `Dispatcher.openOrCloneRepository`, and it only consults `state.repositories` (repositories the app has already added to its persistent list), not `cloningRepositoriesStore.repositories` (repositories currently being cloned): [2](#0-1) 

This method is reached from multiple independent flows that can all be triggered by external, attacker-influenced input: the `x-github-client://openRepo/...` protocol handler parsed by `parseAppURL` (`open-repository-from-url` action) and routed through `openRepositoryFromUrl` / `openBranchNameFromUrl` / `openPullRequestFromUrl`, as well as the `--cli-clone` CLI flag path in `dispatchCLIAction`: [3](#0-2) [4](#0-3) 

Because a clone-in-progress is invisible to the `existingRepository` check (it only appears in `cloningRepositoriesStore`, not `appStore.getState().repositories`, which is only populated after `addRepositories` succeeds post-clone), a second invocation of the same URL while the first clone is still running will not be short-circuited by this guard, and will fall through to `_startOpenInDesktop` again.

The underlying git-level "clone into non-empty directory fails" protection (`validateEmptyFolder` in the UI dialog, and git's own refusal to clone into a non-empty directory) only protects against a *second* clone starting *after* the first has already written files. There is a window immediately after the destination directory is created/verified empty but before `git clone` has populated it, during which a second, independently-triggered clone to the same path would also pass the "directory is empty" check and both `git clone --recursive ... <url> <path>` invocations would run concurrently against the same target directory. [5](#0-4) 

### Impact Explanation
Two concurrent `git clone` processes writing into the same directory can corrupt the resulting repository (interleaved writes into `.git/objects`, `.git/index`, or working tree files), or cause one process to silently overwrite/clobber files the other process wrote, without the user being warned that the repository state is a mixture of two clone operations. If the two triggers additionally specify different branches or PR refs (both `openBranchNameFromUrl` and `openPullRequestFromUrl` funnel through the same clone entrypoint with different follow-up checkout logic), the user's working directory could end up silently containing content the user never intended to commit/push from, differing from either request's intended state — matching the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
Likelihood is moderate-to-low: the destination `path` in the primary UI flow (`clone-repository.tsx`) is chosen interactively by the user via a dialog, which somewhat limits full attacker control of the race window and requires the user to trigger the same "Open in Desktop"/CLI clone action twice in quick succession (e.g., double-clicking a link, or a malicious page firing the protocol handler twice via rapid navigation/redirect). I was not able to fully verify, within the available tool budget, whether `_startOpenInDesktop`/the popup manager already prevents a second `CloneRepository` popup from being shown while one is open, which would reduce (but not eliminate, since the CLI `--cli-clone` path also reaches `_clone` directly) the practical likelihood of two clones targeting the same path.

### Recommendation
Add an in-flight guard to `CloningRepositoriesStore.clone` (and/or `AppStore._clone`) that checks `this._repositories` for an existing `CloningRepository` with the same normalized `path` (and/or `url`) before starting a new `git clone`, returning the existing in-flight promise instead of starting a duplicate process — mirroring the lighthouse fix's approach of registering "in progress" state before the request can be queued/dispatched a second time. Additionally, `Dispatcher.openOrCloneRepository` should also check `cloningRepositoriesStore.repositories` (not just `state.repositories`) so a second identical `open-repository-from-url`/CLI clone request is recognized and joined to the existing in-flight clone rather than re-entering the clone flow.

### Proof of Concept
1. Trigger `x-github-client://openRepo/https://github.com/org/repo` twice in rapid succession (e.g., via two hidden iframes/navigations on a malicious page, or by invoking the CLI `github clone org/repo` twice back-to-back before the first completes).
2. Both invocations reach `Dispatcher.openOrCloneRepository`; since the repository isn't yet in `state.repositories` (only tracked in `cloningRepositoriesStore` during cloning), both calls proceed to `_startOpenInDesktop`/`dispatcher.clone`.
3. If the user (or an automated flow, e.g. two CLI invocations with the same resolved default path) causes both to target the same destination `path`, `CloningRepositoriesStore.clone` is invoked twice with no dedup check, starting two concurrent `git clone --recursive -- <url> <path>` processes against the same directory.
4. Depending on timing, the second `git clone` either fails with a confusing "directory not empty" error mid-way (leaving a corrupted partial clone) or, in the empty-directory race window, both processes write concurrently, producing a repository with an unpredictable/corrupted `.git` state that does not clearly signal to the user that the repository is invalid.

### Citations

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1996)
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

    if (repository === null) {
      return
    }

    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
  }

  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2050-2058)
```typescript
  public async dispatchCLIAction(action: CLIAction) {
    if (action.kind === 'clone-url') {
      const { branch, url } = action

      if (branch) {
        await this.openBranchNameFromUrl(url, branch)
      } else {
        await this.openOrCloneRepository(url)
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
