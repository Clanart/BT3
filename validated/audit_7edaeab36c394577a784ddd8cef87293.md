## Analysis

The reported bug class is: **a security-relevant check that is designed to gate a dangerous behavior, but is silently skipped/bypassed for a specific, security-critical state** (perpetual locks bypassing the "not expired" invariant in `deposit_for()`). The strongest GitHub Desktop analog for this pattern is around Git's `protocol.file.allow` guard for submodules.

### Title
Initial `git clone --recursive` unconditionally disables Git's submodule file-protocol protection, unlike every other submodule-update call path - (File: `app/src/lib/git/clone.ts`)

### Summary
Git ships a built-in protection (`GIT_CLONE_PROTECTION_ACTIVE` / `protocol.file.allow`) that blocks recursive clones from initializing submodules whose URLs point at local `file://` paths, precisely to stop a malicious repository from smuggling a submodule that reads or executes content from arbitrary local paths. Everywhere else in Desktop's codebase this capability is treated as privileged and gated behind an explicit `allowFileProtocol` flag that defaults to `false`. The initial clone path, however, has no such gate at all: it unconditionally forces the protection off.

### Finding Description
`clone()` builds its execution environment with:
```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
```
and then always runs `git clone --recursive`, i.e. submodules from the remote-controlled `.gitmodules` are initialized immediately, with Git's file-protocol guard disabled unconditionally. [1](#0-0) 

In contrast, every subsequent submodule update (`checkoutBranch`, `checkoutCommit`, and `updateSubmodulesAfterOperation`) requires the caller to opt in to `protocol.file.allow=always` via an explicit `allowFileProtocol` parameter, defaulting to `false`, treating this exact capability as one that must not be enabled by default: [2](#0-1) [3](#0-2) 

This is the same broken-invariant shape as the report: a guard exists ("do not allow file-protocol submodules unless explicitly permitted"), but one code path — the one that matters most because it is the very first contact with attacker-supplied `.gitmodules` content during `clone --recursive` — has the guard removed instead of correctly evaluated.

### Impact Explanation
A malicious or compromised repository can declare a submodule with a `file://` (or bare local-path) URL in `.gitmodules`. Because `GIT_CLONE_PROTECTION_ACTIVE` is forced to `'false'` for every clone regardless of context, Git's own defense-in-depth check is defeated at the moment Desktop performs `git clone --recursive`, letting the submodule be initialized from an arbitrary local path (e.g. another local git repository, or a sensitive directory that happens to itself be a git working tree). Depending on what is reachable at that local path, this can lead to disclosure of local repository contents into the newly cloned working directory, or execution of hooks contained in a locally-reachable repository, without any user prompt distinguishing it from a normal clone.

### Likelihood Explanation
High: the only prerequisite is that the victim clones or opens a repository the attacker controls — via `Clone Repository`, the `x-github-client://openRepo/...` deep link, or `--cli-clone`. Since `--recursive` is always passed and the protection flag is unconditionally disabled for `clone()`, no additional user action or confirmation is required, unlike the `allowFileProtocol` opt-in used elsewhere in the same module.

### Recommendation
Stop forcing `GIT_CLONE_PROTECTION_ACTIVE: 'false'` unconditionally in `clone()`. Mirror the `allowFileProtocol` pattern already used in `checkout.ts` / `submodule.ts`: only disable the protection when Desktop has independently validated the operation (e.g., known-safe local-clone flows), and otherwise let Git's default protection stand during the initial `--recursive` clone so that file-protocol submodules are rejected unless the user explicitly consents.

### Proof of Concept
1. Attacker creates a repository containing a `.gitmodules` file with:
   ```
   [submodule "evil"]
       path = evil
       url = file:///Users/victim/some/local/git/repo
   ```
2. Attacker hosts/publishes this repository and lures the victim into cloning it through Desktop's normal Clone dialog or a `x-github-client://openRepo/<url>` deep link.
3. Desktop calls `clone(url, path, options)`, which sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` unconditionally and runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` [4](#0-3) .
4. Because the protection is disabled, Git initializes the `evil` submodule from the local `file://` path without any additional prompt, unlike the deliberate, opt-in-only path used for post-checkout submodule updates [5](#0-4) .

Note: I was not able to trace, within the indexed portions of the codebase, every call site that ultimately determines the `allowFileProtocol` value passed into `checkoutBranch`/`checkoutCommit` (e.g. in `app-store.ts`), so I cannot confirm whether Desktop applies any additional compensating control specifically for the clone path elsewhere in the app (main process, IPC, etc.) that isn't visible in the indexed files. If such a control exists outside of `clone.ts`, it would need to be reviewed to fully assess whether this is exploitable end-to-end. A Devin session with full repository access would be needed to confirm there is no additional gate before `clone()` is invoked.

### Citations

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

**File:** app/src/lib/git/checkout.ts (L102-141)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out branch ${branch.name}`
  const opts = await getCheckoutOpts(
    repository,
    title,
    branch.name,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined,
    `Switching to ${__DARWIN__ ? 'Branch' : 'branch'}`
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)

  // Update submodules after checkout
  await updateSubmodulesAfterOperation(
    repository,
    currentRemote,
    progressCallback
      ? clampProgress<ICheckoutProgress>(
          CheckoutStepWeight,
          1,
          progressCallback
        )
      : undefined,
    'checkout',
    title,
    branch.name,
    allowFileProtocol
  )
```

**File:** app/src/lib/git/submodule.ts (L29-51)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```
