## Title
Submodule `file://` protocol re-enabled during automatic `checkout`/`pull` submodule initialization, defeating Git's built-in SSRF/local-file-read guard - (File: `app/src/lib/git/submodule.ts`)

## Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` accepts an `allowFileProtocol` boolean that, when `true`, injects `-c protocol.file.allow=always` into the `git submodule update --init --recursive` invocation [1](#0-0) . This is the exact policy that upstream Git added (post CVE‑2022‑39253) to stop a malicious `.gitmodules` file from pointing a submodule at a `file://` (or bare local path) URL and having it silently fetched during automatic/recursive submodule operations. `checkoutBranch`/`checkoutCommit` forward this flag straight through to that helper [2](#0-1) .

## Finding Description
`.gitmodules` is a tracked file, fully controlled by whoever pushes to the remote/branch a Desktop user checks out — i.e., attacker-controlled content in a cloned/fetched repository. Git's own defense-in-depth (`protocol.file.allow`) is designed to refuse to auto-follow `file://` submodule URLs unless the user explicitly typed the command themselves, precisely to stop this class of attack (reading files under an attacker-chosen local path, or triggering unwanted local git operations, during what looks like an ordinary branch checkout).

GitHub Desktop's `updateSubmodulesAfterOperation` unconditionally re-enables that protocol whenever it's called with `allowFileProtocol = true` [3](#0-2) . The unit test suite demonstrates this exact code path is exercised for a completely ordinary, attacker-reachable action — checking out a branch that contains an uninitialized submodule — with the flag hard-coded to `true`:

```
await checkoutBranch(
  repository,
  branchWithSubmodule,
  null,
  undefined,
  true
)
``` [4](#0-3) 

Because `branchWithSubmodule` and its `.gitmodules` entry originate from whatever was fetched/cloned, an attacker who controls the repository's branches can supply a submodule URL such as `file:///home/victim/.ssh` (or any other local path) in `.gitmodules`. When a Desktop user checks out that branch, Desktop's own code re-authorizes the `file://` protocol for that `git submodule update --init --recursive`, bypassing Git's own guard rather than the application enforcing its own equivalent trust boundary (e.g., restricting `file://` submodules to paths that are demonstrably part of the same origin or already-trusted local clone hierarchy).

This is structurally identical to the reported bug class: a documented security **policy** (`protocol.file.allow`, Git's analogue to Node's `policy.json`) exists specifically to block a dangerous capability from being reached indirectly, and application code contains a lower-level escape hatch (`-c protocol.file.allow=always`, analogous to `process.binding`) that unconditionally re-enables the blocked capability, defeating the policy's purpose.

## Impact Explanation
If reachable with attacker-controlled `.gitmodules` content, this allows a remote/cloned-repository attacker to make Desktop's git submodule machinery treat an arbitrary local filesystem path as a submodule source — enabling local information disclosure (e.g., "cloning" the contents of a sensitive directory into the submodule's working tree, from where the app or user could inadvertently commit/view it) or unexpected local git operations outside the intended repository boundary. This matches the "file write/read outside the repo" impact category for a cloned-repository attacker.

## Likelihood Explanation
Medium. The mechanism is real and shipped (not test-only code), and the accompanying test proves the flag is meant to be set to `true` for a normal user action (checking out a branch with an as-yet-uninitialized submodule) rather than gated behind an explicit, deliberate user opt-in comparable to running `git submodule update --init` by hand. However, I was not able to fully enumerate every production call site that ultimately supplies `true` for `allowFileProtocol` (e.g., inside `app-store.ts`'s checkout/pull orchestration) within the available search budget, so the exact conditions under which ordinary users hit this path in the shipped app (versus only in tests) could not be fully confirmed. This is a limitation of this analysis, not a claim that the path is inert.

## Recommendation
- Do not blanket re-enable `protocol.file.allow=always` for submodule updates triggered by ordinary checkout/pull. Restrict it (if needed at all) to submodule URLs that resolve within the same trusted repository root, or require explicit user confirmation before following `file://`/local-path submodule URLs discovered in a freshly fetched `.gitmodules`.
- Audit every call site that passes `allowFileProtocol: true` into `updateSubmodulesAfterOperation`/`checkoutBranch`/`checkoutCommit` and confirm none of them are reachable purely by checking out attacker-supplied branches/commits without an explicit, informed user gesture.
- Add a regression test asserting that checking out a branch whose `.gitmodules` contains a `file://` submodule URL fails (or prompts) unless the user has explicitly trusted that submodule source.

## Proof of Concept
1. Attacker pushes a branch to a repository (or a fork the victim adds as a remote) containing a `.gitmodules` entry such as:
   ```
   [submodule "leak"]
     path = leak
     url = file:///home/victim/.ssh
   ```
2. Victim fetches/checks out that branch in GitHub Desktop while the submodule is uninitialized.
3. Desktop calls `checkoutBranch(...)`/`updateSubmodulesAfterOperation(..., true)`, which runs `git -c protocol.file.allow=always submodule update --init --recursive` [5](#0-4) , bypassing Git's own protection and copying `~/.ssh` into the working tree under `leak/`. [6](#0-5)

### Citations

**File:** app/src/lib/git/submodule.ts (L36-54)
```typescript
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

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
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
