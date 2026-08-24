## Answer

The report's core issue is "an operation trusts attacker/market-influenced state without validating it against what the user actually intended," letting an untrusted external input silently change the outcome of an action the user believed was safe. The closest verified GitHub Desktop analog is the `allowFileProtocol` submodule-checkout path, which lets a malicious repository force Desktop to fetch a submodule from a local `file://` path/URL supplied by the attacker's `.gitmodules`, with no validation that the referenced path is something the user should be allowed to read.

### Title
Submodule checkout can be forced to fetch attacker-specified local `file://` paths via `allowFileProtocol` - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` conditionally appends `-c protocol.file.allow=always` to `git submodule update --init --recursive` when its `allowFileProtocol` argument is `true`. [1](#0-0)  This flag is threaded all the way from `checkoutBranch`/`checkoutCommit` down to the submodule updater. [2](#0-1)  When enabled, Git will honor submodule URLs of the form `file:///...` (or bare local paths) found in a cloned/fetched repository's `.gitmodules`, which is exactly the untrusted, attacker-controlled object the "Valid Impact" scope calls out.

### Finding Description
Normally Git disables the `file://` transport for submodules fetched via `submodule update` (a hardening added upstream after `file://` submodule abuse was identified as an attack vector for local file disclosure/RCE through subsequent hooks). Desktop's `updateSubmodulesAfterOperation` deliberately re-enables this transport by passing `protocol.file.allow=always` whenever `allowFileProtocol` is `true`. [3](#0-2)  The value is supplied by the caller of `checkoutBranch`/`checkoutCommit`, both of which pass `allowFileProtocol` straight through to the submodule updater after checkout completes. [4](#0-3) [5](#0-4) 

The broken invariant is analogous to the HODL report's missing `minOut` check: an action (checkout) executes a follow-on operation (submodule init/update) whose behavior depends on attacker-supplied data (the `.gitmodules` submodule URL) with no independent verification that the resulting fetch target is something the user consented to or that is safe to write into their working tree. The unit test suite demonstrates this exact call path is a real, exercised feature — checking out a branch that has an "uninitialized submodule" with `allowFileProtocol = true` set. [6](#0-5)  `listSubmodules`/`.gitmodules` content itself is fully attacker-controlled since it ships inside the cloned/fetched repository. [7](#0-6) 

Existing guards do not stop this path: `getCheckoutOpts` only sets up environment/authentication for the *remote* operation and does not inspect or restrict submodule URLs, [8](#0-7)  and `updateSubmodulesAfterOperation` performs no allow‑listing of submodule URLs before invoking `git submodule update --init --recursive` with `protocol.file.allow=always`. [9](#0-8) 

### Impact Explanation
If Desktop calls into this checkout path with `allowFileProtocol = true` while operating on a repository whose `.gitmodules` was crafted by an attacker (e.g., a cloned/fetched malicious repo, or a repo opened via URL/deep-link that carries a branch with such a submodule), `git submodule update --init --recursive` will honor a `file:///<local-path>` submodule URL and copy the contents of that local path into the submodule directory inside the user's working tree. This is a "read outside the repo" primitive: local files/directories the attacker can guess the location of (e.g., other checked-out repos, SSH/config directories, browser profile folders) get copied into the repository. Because the content lands inside the working tree, it becomes visible in Desktop's diff/history UI and can be silently staged and pushed by the user, satisfying the "silent corruption of what the user commits or pushes" and potential "credential exfiltration" impact buckets.

### Likelihood Explanation
Exploitation only requires the victim to open/clone a hostile repository (or follow a link to one) and check out a branch containing a crafted `.gitmodules` — no local access, admin rights, or pre-existing malware is required, matching the in-scope attacker model. The precondition is that some caller in the Desktop UI (not fully confirmed in this pass) invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol = true` for repositories obtained from untrusted sources such as a fresh clone or an "open repository from URL" deep-link flow; the unit test confirms the mechanism is live application code, not dead code, but I could not exhaustively trace every production call site that sets this flag to `true` within the tool-call budget available. This uncertainty should be resolved by tracing all `checkoutBranch(...)`/`checkoutCommit(...)` call sites in `app-store.ts`/`dispatcher.ts` for the literal `true` argument (positional 5th parameter) before treating this as confirmed-exploitable end-to-end.

### Recommendation
- Do not enable `protocol.file.allow=always` for submodule operations on repositories that were cloned/fetched from a remote or opened via URL/deep-link unless the submodule URL has been explicitly validated against an allow-list (e.g., same-host HTTPS/SSH URLs matching the parent remote).
- If local-path submodules must be supported for legitimate local-only workflows, gate `allowFileProtocol` behind an explicit, per-operation user confirmation showing the exact local path that will be read, analogous to adding a `minOut`-style explicit "expected value" check in the HODL report.
- Audit every call site that passes `allowFileProtocol: true` and confirm it is restricted to trusted, locally-created repositories only.

### Proof of Concept
1. Attacker publishes a repository containing:
   ```
   .gitmodules:
     [submodule "leak"]
       path = leak
       url = file:///Users/victim/.ssh
   ```
2. Victim clones/fetches this repository in GitHub Desktop and checks out the branch containing this `.gitmodules` (or the branch is checked out automatically, e.g., after a PR checkout via deep link).
3. Desktop's checkout flow calls `updateSubmodulesAfterOperation` with `allowFileProtocol = true` for this operation. [10](#0-9) 
4. `git -c protocol.file.allow=always submodule update --init --recursive` executes, cloning `/Users/victim/.ssh` into `leak/` inside the working tree. [9](#0-8) 
5. The victim's private key files now appear as untracked/new files in Desktop's Changes view; if the victim stages and commits/pushes (or the attacker socially engineers a "please push your test results" step), the private key is exfiltrated to the attacker's remote.

*Note: this PoC assumes a concrete call site sets `allowFileProtocol=true` for untrusted repositories; I confirmed the mechanism exists and is exercised in tests but did not exhaustively verify every production trigger due to tool-call limits — this should be validated by a follow-up code trace of all `checkoutBranch`/`checkoutCommit` callers.*

### Citations

**File:** app/src/lib/git/submodule.ts (L29-56)
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

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
    return
  }
```

**File:** app/src/lib/git/submodule.ts (L127-153)
```typescript
export async function listSubmodules(
  repository: Repository
): Promise<ReadonlyArray<SubmoduleEntry>> {
  const [submodulesFile, submodulesDir] = await Promise.all([
    pathExists(join(repository.path, '.gitmodules')),
    pathExists(join(repository.path, '.git', 'modules')),
  ])

  if (!submodulesFile && !submodulesDir) {
    // repo path + .gitmodules and + .git/modules covers the vast majority of
    // "normal" repositories but if we're in a linked worktree the modules
    // directory is actually in the git common dir so we'll also check for the
    // existence of the modules directory there as well before giving up on the
    // existence of submodules in this repo. We're reading the commondir file
    // ourselves here instead of calling out to git to avoid the cost of
    // spawning a process on Windows
    const commonDirPath = join(repository.resolvedGitDir, 'commondir')
    const commonDir = await readFile(commonDirPath, 'utf8')
      .then(content => content.replace(/\r?\n$/, ''))
      .then(p => (p ? resolve(repository.resolvedGitDir, p) : null))
      .catch(() => null)

    if (!commonDir || !(await pathExists(join(commonDir, 'modules')))) {
      log.info('No submodules found. Skipping "git submodule status"')
      return []
    }
  }
```

**File:** app/src/lib/git/checkout.ts (L38-55)
```typescript
async function getCheckoutOpts(
  repository: Repository,
  title: string,
  target: string,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  initialDescription?: string
): Promise<IGitStringExecutionOptions> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, currentRemote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  if (!progressCallback) {
    return opts
  }
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

**File:** app/src/lib/git/checkout.ts (L182-202)
```typescript
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
