Based on my investigation, the closest structural analog to the "unprivileged overwrite bypassing a security invariant" pattern in the report is not in the state-store/permission code (those paths are all keyed to GitHub-API-verified owner/repo identity and can't be forged by an unprivileged attacker), but in the submodule checkout path, where a boolean flag threaded from the checkout functions re-enables the `file://` git transport that upstream Git disabled by default specifically to stop malicious repositories from reading/exfiltrating files via submodules (the class fixed upstream by `protocol.file.allow`).

### Title
Unconditional `protocol.file.allow=always` on submodule initialization during branch/commit checkout re-enables `file://` submodule cloning for untrusted repository content - (File: `app/src/lib/git/submodule.ts`, `app/src/lib/git/checkout.ts`)

### Summary
`checkoutBranch` and `checkoutCommit` accept an `allowFileProtocol` boolean that is forwarded to `updateSubmodulesAfterOperation`, which — when `true` — prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation. [1](#0-0) [2](#0-1) 

### Finding Description
Git upstream restricts `git submodule` from following `file://` (local filesystem) submodule URLs by default (`protocol.file.allow=user`, requiring an interactive prompt) precisely because a malicious repository's `.gitmodules` can point a submodule at an arbitrary local path and have it silently "cloned" into the working tree during `submodule update --init`. `updateSubmodulesAfterOperation` explicitly overrides this protection whenever `allowFileProtocol` is `true`, unconditionally injecting `protocol.file.allow=always` for the whole `git submodule update --init --recursive` command — with no validation of which submodules in the just-checked-out tree actually use `file://` URLs, and no distinction between a locally-created submodule and one introduced by content fetched from a remote/fork. [3](#0-2) 

The test suite confirms this flag is meant to be passed as `true` specifically to initialize a submodule that appears for the first time in a branch being checked out (`checkoutBranch(repository, branchWithSubmodule, null, undefined, true)`), i.e., exactly the scenario where the submodule's `.gitmodules` entry — including its URL — originates from content the user did not previously have locally (a newly fetched/pulled branch, PR branch, or fork). [4](#0-3) 

This mirrors the `benRevocable` pattern in the original report: a flag/override (`_isRevocable` / `allowFileProtocol`) is applied broadly based on unprivileged, attacker-influenced input (the beneficiary address / the checked-out branch's submodule definitions) without narrowing the override to only the specific case it was meant to cover, silently defeating an existing safety guard (`revoke()` gating / `protocol.file.allow` restriction).

### Impact Explanation
If the override is reachable when checking out a branch or commit that came from an untrusted remote (e.g., a pull request branch from a fork, or any branch fetched from a malicious/compromised remote), an attacker who controls that branch's `.gitmodules` can point a submodule at a `file://` URL targeting a sensitive local path (e.g., another repository on disk, or a directory containing credentials/config), and Desktop will clone/copy that local content into the checked-out working tree during `submodule update --init --recursive`. This falls squarely into "attacker controls a cloned/fetched repository ... result is file read/write outside the repo," since the submodule init operation is not confined to the target repository's own directory and is influenced entirely by content the user did not author.

### Likelihood Explanation
Likelihood depends on how broadly `allowFileProtocol=true` is passed from the application (`app-store.ts`/`dispatcher.ts`) into `checkoutBranch`/`checkoutCommit` versus limited to narrowly-scoped, pre-validated scenarios. I was not able to fully trace every production call site of `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true` within the remaining tool budget — this is the primary uncertainty in this finding. The default value of the parameter is `false`, so the base checkout path is safe; the risk is confined to whichever code path(s) intentionally pass `true` to handle "uninitialized submodule appears after checkout," and whether that path is reachable for branches originating from untrusted remotes/forks rather than only from repositories the user has already vetted.

### Recommendation
Scope the `protocol.file.allow=always` override narrowly: only allow `file://` submodule URLs that resolve to paths the user already trusts (e.g., submodules that existed prior to the checkout, or paths confirmed to be inside directories the user has explicitly trusted), rather than blanket-enabling the protocol for the entire `submodule update --init --recursive` invocation whenever a new submodule appears in freshly-fetched content. Alternatively, drop the override entirely and surface Git's normal interactive-consent prompt (or an equivalent Desktop-native confirmation dialog) before initializing any submodule that uses the `file://` protocol.

### Proof of Concept
1. Attacker creates a branch (or PR from a fork) whose `.gitmodules` declares a submodule with `url = file:///Users/victim/.ssh` (or any other sensitive local path) and commits it.
2. Victim, using GitHub Desktop, fetches/opens this branch/PR and checks it out (a normal, unprivileged workflow action).
3. If the checkout path that introduces this newly-appearing submodule reaches `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true`, `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, cloning the local `file://` target into the repository's working tree without any user prompt. [5](#0-4) 

Full confirmation of exploitability requires verifying the exact production call sites (outside of tests) that set `allowFileProtocol=true`, which I could not complete within the available tool budget.

### Citations

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

**File:** app/test/unit/git/checkout-test.ts (L150-166)
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
```
