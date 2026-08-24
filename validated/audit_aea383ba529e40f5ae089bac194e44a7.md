Based on my investigation, I found a plausible structural analog, but I want to be upfront about the limits of what I could confirm with the available searches.

### Title
Submodule checkout can be forced to use `protocol.file.allow=always`, bypassing Git's local-file submodule protection - ([File: app/src/lib/git/submodule.ts])

### Summary
The external report's broken invariant is: a security check exists to keep a specific "safe" state guaranteed (liquidator only holding cash), but a caller-controlled bypass path (fresh wrapped-token deposit) lets an untrusted party invalidate that guarantee without triggering the expensive/safe verification. The Desktop analog is `updateSubmodulesAfterOperation`, which can be told to disable Git's own hardening against submodules that use the `file://` protocol by passing `-c protocol.file.allow=always` to `git submodule update --init --recursive`.

### Finding Description
`updateSubmodulesAfterOperation` accepts an `allowFileProtocol` boolean; when `true` it prepends `-c protocol.file.allow=always` to the submodule-update invocation: [1](#0-0) 

This flag flows from `checkoutBranch`/`checkoutCommit` in `app/src/lib/git/checkout.ts`, which default it to `false` but allow callers to pass `true`: [2](#0-1) [3](#0-2) 

Git added `protocol.file.allow` protection (defaulting to disallow, post CVE-2022-39253) specifically so that a malicious repository's `.gitmodules` cannot point a submodule at a `file://` path and have Git silently read/copy content from that local path during clone/checkout of an otherwise-untrusted repository. The unit test suite in this codebase demonstrates the `allowFileProtocol=true` path being exercised specifically when Desktop needs to bootstrap an "uninitialized submodule" during a branch checkout: [4](#0-3) [5](#0-4) 

The invariant that should hold is: Desktop should never re-enable `file://` submodule fetching for a repository whose content (including `.gitmodules`) originates from an untrusted clone/fetch, because the submodule URL is attacker-controlled data embedded in the tracked tree, not something the user typed. If any call site in `app-store.ts`/`dispatcher.ts` sets `allowFileProtocol=true` when checking out a branch from an attacker-supplied repository (e.g., to auto-initialize a previously-uninitialized submodule), a malicious `.gitmodules` entry such as `url = file:///Users/victim/.ssh` or `file:///Users/victim/some-other-repo` would cause `git submodule update --init --recursive` to copy content from that local path into the tracked submodule directory of the checked-out repository.

### Impact Explanation
If exploitable from a production call path (not just test helpers), a malicious repository could exfiltrate arbitrary local files reachable by path to which the victim's OS user has read access, by declaring a submodule whose URL is `file://<local path>` and whose content ends up written into the working tree as tracked files. Because the checkout is user-initiated but the *content* of the submodule (the exfiltrated files) is Git-tracked, the victim could then unknowingly commit and push that content to the attacker's remote — this is exactly the "silent corruption of what the user commits or pushes" and "file read outside the repo" categories called out as valid impact.

### Likelihood Explanation
I could not fully confirm, within the available searches, that a *production* (non-test) call path in `app-store.ts` or `dispatcher.ts` passes `allowFileProtocol=true` when checking out branches from repositories whose `.gitmodules` content is untrusted (e.g., freshly cloned/fetched from a remote). The confirmed evidence of `allowFileProtocol=true` usage is in the test helper `setupRepositoryWithUninitializedSubmodule` and the corresponding checkout test, both of which use local, controlled fixture repositories. Without the ability to trace every `checkoutBranch`/`checkoutCommit` call site in `app-store.ts` back to its trigger condition, I cannot state with certainty whether this flag is gated on "remote is a local/file path the user explicitly added" versus being reachable for any cloned GitHub repository containing an uninitialized submodule pointing at `file://`.

### Recommendation
- Confirm every production call site of `checkoutBranch`/`checkoutCommit`/`updateSubmodulesAfterOperation` that can pass `allowFileProtocol=true`, and restrict it strictly to repositories/remotes that are already local filesystem paths explicitly chosen by the user (never to submodule URLs sourced from a cloned/fetched, potentially untrusted `.gitmodules`).
- Never let `allowFileProtocol` be derived from or triggered by content inside the untrusted repository itself (e.g., "this branch has an uninitialized submodule" should not, by itself, justify re-enabling `file://` submodules).
- If local-path submodules must be supported for legitimate multi-repo/monorepo workflows, restrict `protocol.file.allow` to an explicit user opt-in (e.g., a repository setting toggled after Desktop warns about the specific local path being referenced), not a blanket "always" flag threaded silently through checkout.

### Proof of Concept
Not independently verified against the production code path within the given search budget. A conceptual PoC, contingent on confirming a production call passes `allowFileProtocol=true` during checkout of an attacker-controlled repo:
1. Attacker creates a repository containing a branch with a `.gitmodules` entry: `url = file:///Users/victim/.ssh` (or another sensitive local path), and commits this branch with the submodule left "uninitialized" in the tree.
2. Victim clones/fetches the repository in GitHub Desktop and checks out that branch.
3. If the checkout path that runs `updateSubmodulesAfterOperation` for this scenario invokes it with `allowFileProtocol=true` (as exercised by the test at `app/test/unit/git/checkout-test.ts:150-189`), Git executes `submodule update --init --recursive` with `protocol.file.allow=always`, cloning the victim's local `file://` path into the submodule directory of the tracked repository.
4. The victim, unaware, stages/commits/pushes the resulting directory, exfiltrating local file contents to the attacker's remote.

Given the unresolved uncertainty about the exact triggering conditions in production code, this should be treated as a **lead for further investigation** rather than a fully confirmed vulnerability. I'd recommend a follow-up session with full repository access to trace all `checkoutBranch`/`checkoutCommit` call sites in `app-store.ts` and `dispatcher.ts` to determine whether `allowFileProtocol=true` is ever reachable from checking out a branch of a remote, untrusted repository.

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

**File:** app/src/lib/git/checkout.ts (L163-202)
```typescript
export async function checkoutCommit(
  repository: Repository,
  commit: CommitOneLine,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out ${__DARWIN__ ? 'Commit' : 'commit'}`
  const target = shortenSHA(commit.sha)
  const opts = await getCheckoutOpts(
    repository,
    title,
    target,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, commit.sha]

  await git(args, repository.path, 'checkoutCommit', opts)

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
    target,
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

**File:** app/test/helpers/repositories.ts (L288-321)
```typescript
export async function setupRepositoryWithUninitializedSubmodule(
  t: TestContext
): Promise<Repository> {
  const repo = await setupTwoCommitRepo(t)

  // Create a submodule repository
  const submoduleRepo = await setupTwoCommitRepo(t)

  // Create a new branch and add the submodule
  await exec(['checkout', '-b', 'branch-with-submodule'], repo.path)

  await exec(
    [
      '-c',
      'protocol.file.allow=always',
      'submodule',
      'add',
      submoduleRepo.path,
      'test-submodule',
    ],
    repo.path
  )
  await exec(['commit', '-m', 'Add submodule'], repo.path)

  // Go back to main branch
  await exec(['checkout', 'master'], repo.path)

  // Remove the .git/modules directory for the submodule to make it uninitialized
  const modulesPath = join(repo.path, '.git', 'modules', 'test-submodule')
  await rm(modulesPath, { recursive: true, force: true })
  await rm(join(repo.path, 'test-submodule'), { recursive: true, force: true })

  return repo
}
```
