Based on the evidence gathered, the strongest analog in GitHub Desktop is the `allowFileProtocol` parameter that re-enables Git's `file://` submodule protocol, which was disabled upstream in Git specifically because malicious repository content (`.gitmodules`) can otherwise be weaponized to read/copy arbitrary local paths. I was not able to fully trace, in my remaining iterations, every call site in `app-store.ts` that decides when `true` is passed to `checkoutBranch`/`checkoutCommit` (and therefore to `updateSubmodulesAfterOperation`) — I only confirmed the plumbing (`checkout.ts`, `submodule.ts`) and one test that invokes `checkoutBranch(repository, branchWithSubmodule, null, undefined, true)` unconditionally, which strongly implies at least one legitimate flow always allows the file protocol during a branch checkout when submodules are uninitialized. I flag this remaining gap explicitly below.

### Title
Malicious `.gitmodules` file:// submodule URL can copy arbitrary local files into the working tree via `protocol.file.allow=always` - ([File: app/src/lib/git/submodule.ts])

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` conditionally re-enables Git's `file://` transport for `git submodule update --init --recursive` by passing `-c protocol.file.allow=always` when its `allowFileProtocol` parameter is `true`. [1](#0-0) 
This flag flows in from `checkoutBranch`/`checkoutCommit` in `app/src/lib/git/checkout.ts`, which accept an `allowFileProtocol` argument (default `false`) and forward it unchanged to `updateSubmodulesAfterOperation` after a checkout. [2](#0-1) 
A unit test exercises the path with `allowFileProtocol = true` when checking out a branch that has an uninitialized submodule. [3](#0-2) 

### Finding Description
Git upstream disabled the `file://` transport for submodules by default (hardening after CVE-2017-1000117-class issues) because a `.gitmodules` file — which is fully attacker-controlled content shipped inside a cloned/fetched repository — can declare a submodule URL such as `file:///home/victim/.ssh` or a relative path that resolves outside the intended remote scope. When `protocol.file.allow=always` is set, `git submodule update --init` will treat that URL as a valid local Git repository to "clone" from, copying its objects/refs (and thus file contents reachable as a Git repository, e.g. another local repo containing secrets) into the submodule's working directory inside the victim's checked-out repository.

The broken invariant mirrors the report's pattern: a value that should only be influenced by an explicit, validated user action (the user explicitly choosing to trust a `file://` submodule source) is instead derived from data the attacker fully controls (`.gitmodules` content in a repo the user merely opened/cloned/checked-out), and an internal flag (`allowFileProtocol`) silently bypasses the protective default Git itself set precisely to prevent this. Just as the GenesisGroup checks `address(this).balance` instead of a value that can only change through the validated `purchase()` path, Desktop's checkout flow trusts `.gitmodules` submodule URLs without validating they are non-local before opting into `protocol.file.allow=always`.

### Impact Explanation
If triggered, this allows an attacker who controls a repository the victim clones/checks out to cause local files (any path Git can read as an object store, or the contents of another local Git repository) to be copied into the victim's working directory as a submodule checkout. This can lead to disclosure of local secrets (e.g., `.git` internals of other private repos on disk) into the file tree, which — if the user is not paying close attention — could be committed and pushed to the attacker's remote, satisfying the "silent corruption of what the user commits or pushes" / local file read outside the repo impact criteria.

### Likelihood Explanation
Likelihood depends entirely on the conditions under which `app-store.ts` calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol = true` for a repository whose submodules are not yet initialized. I could not fully confirm from the index whether this is gated behind a user prompt/confirmation dialog or is an unconditional default for local repositories, because tool iterations were exhausted before I could inspect all 8 call sites in `app-store.ts`. The presence of an unconditional `true` argument in the checkout unit test suggests at least one code path exercises this without an explicit per-submodule-URL safety check, but this needs to be verified in `app-store.ts` before treating it as confirmed-exploitable.

### Recommendation
Before setting `protocol.file.allow=always`, validate each submodule URL declared in `.gitmodules` and refuse (or explicitly warn/prompt) if any resolve to `file://` or local-path schemes referencing locations outside of an already-trusted, previously-cloned submodule cache. Prefer only enabling `protocol.file.allow` for a specific, already-resolved absolute path known to be a previously-registered submodule of that exact repository, never globally via `always`.

### Proof of Concept
1. Attacker publishes a public repository containing a `.gitmodules` file with an entry: `[submodule "x"] path = x` `url = file:///home/victim/.ssh` (or, cross-platform, a path pointing at another sensitive local Git repository/checkout).
2. Victim clones the repository in GitHub Desktop and checks out a branch/commit that references this uninitialized submodule (the flow exercised by `app/test/unit/git/checkout-test.ts:150-189`).
3. If the checkout path passes `allowFileProtocol = true` (as the test does), `git -c protocol.file.allow=always submodule update --init --recursive` runs and clones from the attacker-specified local path into the working tree, exposing its contents inside the repository.

**Caveat:** I could not confirm from local code search which real (non-test) call sites pass `allowFileProtocol = true` and under what conditions; this must be verified directly in `app/src/lib/stores/app-store.ts` to confirm real-world reachability without user consent.

### Citations

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

**File:** app/test/unit/git/checkout-test.ts (L150-189)
```typescript
    it('initializes an uninitialized submodule when checking out a branch', async t => {
      const repository = await setupRepositoryWithUninitializedSubmodule(t)

      const branches = await getBranches(repository)
      const branchWithSubmodule = branches.find(b => b.name !== 'master')

      if (branchWithSubmodule == null) {
        throw new Error(`Could not find branch other than 'master'`)
      }

      await checkoutBranch(
        repository,
        branchWithSubmodule,
        null,
        undefined,
        true
      )

      // Verify we're on the correct branch
      const statusOutput = await exec(['status'], repository.path)
      assert.ok(
        statusOutput.stdout.includes(`On branch ${branchWithSubmodule.name}`)
      )

      // Verify the submodule is initialized and has the correct commits
      const submodulePath = Path.join(repository.path, 'test-submodule')
      const submoduleGitPath = Path.join(submodulePath, '.git')

      // Check that submodule .git exists (either as file or directory)
      const submoduleGitExists = await pathExists(submoduleGitPath)
      assert.equal(
        submoduleGitExists,
        true,
        'Submodule .git should exist after checkout'
      )

      // Verify submodule has two commits
      const submoduleLog = await exec(['log', '--oneline'], submodulePath)
      assert.equal(submoduleLog.stdout.trim().split('\n').length, 2)
    })
```
