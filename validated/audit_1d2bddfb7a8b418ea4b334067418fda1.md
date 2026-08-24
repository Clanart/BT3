## Analysis

The external report's underlying pattern is: **a normally-guarded, security-sensitive setting is deliberately relaxed by "trusted" code, letting attacker-supplied content reach an unsafe execution/read path that upstream protections exist specifically to block.** In `multi-proxy-rubic` that's the `Owner` bypassing audited-facet checks; the closest concrete analog in GitHub Desktop is Git's *clone protection* being explicitly disabled during recursive clones.

### Title
Recursive clone explicitly disables Git's clone protection (`GIT_CLONE_PROTECTION_ACTIVE=false`), re-enabling local file/path disclosure via crafted submodules - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and always passes `--recursive` when cloning any URL a user opens in Desktop [1](#0-0) . `GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git itself introduced to guard against clone-time attacks (e.g. symlinked/hardlinked `.git` entries, submodule hook/path tricks) that can leak or corrupt files outside the intended destination. Forcing it to `'false'` turns that protection off for every clone Desktop performs, at exactly the moment (`--recursive`, i.e. automatic submodule initialization) where the attacker-controlled repository content (its `.gitmodules` file and nested submodule commits, both fully attacker-controlled) is used to decide what gets fetched/linked onto the user's disk.

### Finding Description
- `clone()` builds the environment via `envForRemoteOperation(url)` and then overrides it with `GIT_CLONE_PROTECTION_ACTIVE: 'false'` before invoking `git … clone --recursive -- url path` [1](#0-0) [2](#0-1) .
- Submodule initialization/update elsewhere in the codebase is explicitly aware that `file://` submodule URLs are dangerous and normally blocked by Git, gating them behind an `allowFileProtocol` flag that adds `-c protocol.file.allow=always` only when explicitly requested [3](#0-2) [4](#0-3) . This shows the team is aware of and tries to gate submodule/file-protocol risk in the checkout path — but the initial `clone` path bypasses Git's own built-in clone-protection guard entirely and unconditionally, regardless of user consent, for every remote the user clones (a repository the attacker fully controls, since it's their own hosted repo/URL).
- `GIT_CLONE_PROTECTION_ACTIVE=false` disables Git's mitigation (the same class of bug fixed upstream for CVE-2022-39253) against maliciously crafted repositories that abuse recursive submodule cloning together with symlinked/hardlinked paths or crafted `.gitmodules` entries to write or expose files outside the intended submodule/working-tree boundary. By forcing this off, Desktop removes a defense-in-depth check that Git maintainers added specifically to stop attacker-controlled repository content from escaping the clone destination during `--recursive` operations.
- The broken invariant: "cloning a repository, including all of its submodules, must not read or write files outside the destination path." Git's own clone-protection flag is the guard for this invariant; Desktop actively disables it on every clone.

### Impact Explanation
An attacker who controls a repository (the primary attacker surface explicitly allowed under the "cloned/fetched repository" category) can craft `.gitmodules` entries and nested submodule commits designed to exploit the clone-protection checks that `GIT_CLONE_PROTECTION_ACTIVE` guards against. With protection disabled, a victim who simply clones the attacker's repository in Desktop (a completely ordinary, expected action) can have files written/linked outside the intended repository directory as part of the automatic `--recursive` submodule initialization, i.e., file write/disclosure outside the repo boundary — matching the "Valid Impact" criteria for this report (file write/read outside the repo, silent corruption of on-disk state) without requiring any local/physical access, admin rights, or social engineering beyond "clone this URL," which is Desktop's core workflow.

### Likelihood Explanation
High for exposure: the code path is taken unconditionally for every clone in the app (there is no opt-out, no user-visible warning, and no distinction between trusted vs. untrusted remotes) [1](#0-0) . The only requirement is that the victim clones a URL supplied/controlled by the attacker — a normal, expected Desktop action.

### Recommendation
Do not hardcode `GIT_CLONE_PROTECTION_ACTIVE: 'false'`. Let Git's clone protection run (its default is to abort/report unsafe recursive-clone conditions), and if Desktop needs a specific narrow permission (e.g., for known-safe local test fixtures), scope that override to just those cases rather than applying it globally to every clone the user performs from arbitrary remote URLs. This mirrors the report's own recommendation to resolve the "resolve the more permissive path" finding first, since the impact here — like the DiamondCutFacet case — stems from a single piece of "trusted" app code (the clone helper) unilaterally disabling a security control that the underlying trusted engine (Git) provides.

### Proof of Concept
1. Attacker publishes a Git repository containing `.gitmodules` entries and nested submodule commits crafted to trigger the class of clone-time path/hardlink issue that `GIT_CLONE_PROTECTION_ACTIVE` is designed to catch (the same shape of attack Git's upstream clone-protection feature was added to block).
2. Victim opens GitHub Desktop and clones the attacker's repository URL via the normal "Clone repository" flow, which calls `clone()` in `app/src/lib/git/clone.ts`.
3. Because `GIT_CLONE_PROTECTION_ACTIVE` is forced to `'false'` and `--recursive` is always passed, Git performs the recursive submodule clone without the safety checks it would otherwise perform, allowing the crafted repository content to affect files outside the clone destination during submodule initialization.

Note: I was not able to fully verify, with the remaining evidence gathered, the exact current upstream Git behavior differences enabled/disabled by `GIT_CLONE_PROTECTION_ACTIVE` in the bundled dugite/Git version, nor whether any additional compensating checks exist elsewhere in Desktop's clone flow that might partially mitigate this. This would benefit from further investigation via a Devin session with full repository and Git version access to confirm the exact exploitable primitive.

### Citations

**File:** app/src/lib/git/clone.ts (L81-93)
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
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/git/submodule.ts (L36-51)
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
