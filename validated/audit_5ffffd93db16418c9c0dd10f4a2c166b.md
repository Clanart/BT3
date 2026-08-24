### Title
Submodule `file://` protocol re-enablement (`protocol.file.allow=always`) applied indiscriminately during recursive submodule init/checkout - (File: `app/src/lib/git/submodule.ts`)

### Summary
The reported Alchemix bug is a case where a permissioned action (creating a governance proposal) is silently permitted because the threshold check is computed from state that starts at zero/empty (`totalSupply == 0`), and nothing forces that state to be non-empty before the check is trusted. The Desktop analog with the same shape — "a security-relevant gate is driven by a piece of repository state that an untrusted repo author fully controls, and when that state takes a particular (attacker-chosen) value, a hardening control is switched off for the whole operation" — is the `allowFileProtocol` flag threaded through `checkoutBranch` / `checkoutCommit` (`app/src/lib/git/checkout.ts`) into `updateSubmodulesAfterOperation` (`app/src/lib/git/submodule.ts`).

### Finding Description
`updateSubmodulesAfterOperation` builds the `git submodule update --init --recursive` command and conditionally prepends `-c protocol.file.allow=always`: [1](#0-0) 

Git (since 2.38, the fix for CVE-2022-39253) defaults `protocol.file.allow` to `user`/deny for submodules specifically to stop a malicious repository from declaring a `file://` submodule URL that would make a victim's `git submodule update --init --recursive` read files from elsewhere on the victim's own filesystem into the checkout. Desktop's `allowFileProtocol` parameter exists to override that protection — but it is passed as a single boolean that applies **globally to the whole recursive update** (`--recursive`, i.e., all submodules and their nested submodules), not scoped to only the one submodule that needed the override: [2](#0-1) [3](#0-2) 

The test fixtures show the intended trigger: a branch containing a submodule whose `.git/modules` entry doesn't yet exist locally (an "uninitialized" submodule, `git submodule status` `-` prefix), which is exactly the kind of repository content an untrusted repo/branch author fully controls: [4](#0-3) [5](#0-4) 

Because `--recursive` walks into every nested `.gitmodules`, once the flag is flipped to `true` for a checkout it removes Git's file-protocol guard for **every** submodule reachable from that checkout — including nested submodules an attacker adds purely to exploit the now-disabled guard, e.g. a nested `.gitmodules` entry with `url = file:///home/<user>/.ssh` or another out-of-repo path. This mirrors the governance bug precisely: the presence/absence of a piece of attacker-controlled state (here: "does this submodule look uninitialized") flips a protective check off for a broader scope than intended, and the flip is driven entirely by content the attacker supplies in the cloned/fetched repository.

### Impact Explanation
If Desktop decides to set `allowFileProtocol = true` whenever a checkout would need to initialize a submodule (the scenario the test fixtures exist to cover), a malicious repository can craft nested submodules that abuse the re-enabled `file://` protocol to have Git copy arbitrary local files reachable via a `file://` URL into the working directory during an ordinary `checkout`/`pull`/`clone` flow. That copied content becomes files "in the repo" from the user's point of view and can be staged, viewed, committed, and pushed — i.e., silent exfiltration of local files outside the repo into what the user then commits/pushes, which matches the stated valid-impact categories ("attacker controls a cloned/fetched repository ... result is ... file read outside the repo ... or silent corruption of what the user commits or pushes").

### Likelihood Explanation
Medium-low confidence overall: I confirmed the mechanism (the flag, its effect on the git invocation, and that it is scoped to the whole `--recursive` update) directly in `submodule.ts` and `checkout.ts`, and confirmed via the shipped unit tests that "uninitialized submodule" is the scenario this flag is meant to solve. However, I was not able to trace, within the available tool budget, the exact production call site (in `app-store.ts` / `dispatcher.ts`) that decides the boolean value passed for `allowFileProtocol` on a real user checkout — only the test helpers exercise it directly with a hard-coded `true`. It is therefore unconfirmed whether the production heuristic (a) is based purely on local submodule-initialization state that a malicious repo can set up unilaterally, and (b) whether it is scoped per-submodule anywhere before reaching this function. This is the key uncertainty that should be resolved before treating this as a confirmed, exploitable vulnerability.

### Recommendation
- Verify (in `app-store.ts`/`dispatcher.ts`) exactly what state decides `allowFileProtocol`, and confirm it can be influenced by an untrusted repository/branch (e.g., a submodule commit that appears "uninitialized").
- If confirmed, avoid blanket `protocol.file.allow=always` for `--recursive` updates. Instead, resolve and allow-list only the specific submodule path(s)/URL(s) that legitimately need local-path support (e.g., same-host relative paths already inside the repo), and continue to deny `file://` for any other/nested submodule.
- Add a check that rejects `file://` submodule URLs pointing outside the repository's own directory tree even when file protocol is allowed, mirroring the intent of Git's CVE-2022-39253 fix.

### Proof of Concept
Not independently reproduced end-to-end due to the unresolved call-site question above. The concrete, reproducible half of the chain is:
1. Using the existing test helper `setupRepositoryWithUninitializedSubmodule` pattern, craft a repository/branch where a submodule is "uninitialized" and its nested `.gitmodules` (once initialized) contains a submodule entry with `url = file:///path/outside/the/repo` (e.g., pointing at a sensitive local directory).
2. Call `checkoutBranch(repository, branchWithSubmodule, null, undefined, /*allowFileProtocol=*/true)` as done in `app/test/unit/git/checkout-test.ts:160-166` — this reproduces `git -c protocol.file.allow=always submodule update --init --recursive`, which will happily clone the nested `file://` submodule.
3. The unresolved step is confirming that ordinary user actions (not test code) reach this call with `allowFileProtocol=true` based solely on attacker-supplied repository content, which would complete the exploit chain from "victim clones/checks out a malicious repo" to "local file copied into the checkout."

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

**File:** app/src/lib/git/checkout.ts (L102-146)
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

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```

**File:** app/src/lib/git/checkout.ts (L163-207)
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

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
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
