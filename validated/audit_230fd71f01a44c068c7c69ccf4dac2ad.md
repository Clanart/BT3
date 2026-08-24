Based on my research, I found a concrete analog: `checkoutBranch`/`checkoutCommit` in `app/src/lib/git/checkout.ts` call `updateSubmodulesAfterOperation` with an `allowFileProtocol` flag that, when `true`, adds `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation, re-enabling `file://` submodule URLs that upstream Git disabled by default (post CVE-2022-39253) for security reasons. [1](#0-0) [2](#0-1) 

### Title
Attacker-controlled `.gitmodules` combined with `allowFileProtocol` re-enables disabled `file://` submodule cloning during checkout - (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` conditionally passes `-c protocol.file.allow=always` to `git submodule update --init --recursive` when its `allowFileProtocol` parameter is `true`. This deliberately overrides Git's own hardened default (`protocol.file.allow=user`, changed after CVE-2022-39253) which normally blocks a cloned repository's `.gitmodules` from specifying `file://` submodule URLs. `.gitmodules` is a tracked, attacker-controlled file, so any repository the user clones/checks out can define a submodule URL pointing at an arbitrary local path (e.g., another local Git repository, or a path an attacker can predict/prime on the victim's machine). [3](#0-2) 

### Finding Description
Git added `protocol.file.allow` (default `user`, i.e. disabled for submodules populated from a fetched repo) specifically to stop a malicious repository from using a `file://` submodule URL to make Git clone an arbitrary local directory (including outside the intended working tree) into the victim's checkout — leaking local repository contents/history into the attacker-visible working copy, or triggering execution of hooks/build scripts contained in that local directory once it's "checked out" as a submodule. Desktop reintroduces this exact bypass by hard-coding `protocol.file.allow=always` whenever `allowFileProtocol` is `true`, letting Git honor `file://` submodule URLs declared in the untrusted, cloned `.gitmodules`. [4](#0-3) 

`checkoutBranch` and `checkoutCommit` both take an `allowFileProtocol: boolean = false` parameter and forward it unchanged to `updateSubmodulesAfterOperation` after running `git checkout`, so whenever a code path in the app sets this flag to `true` for a branch/commit switch, any newly-checked-out `.gitmodules` entries with `file://` URLs are honored. [5](#0-4) [6](#0-5) 

Existing guards do not stop this path: `envForRemoteOperation`/`getFallbackUrlForProxyResolve` only manage credentials/proxy resolution for the operation, not the protocol allow-list, and `isClonePathSensitive` in `clone.ts` only checks the *top-level clone destination*, not paths reachable via submodule `file://` URLs during a later checkout/submodule update. Nothing in `submodule.ts` validates or restricts the submodule URL scheme or target path before appending `protocol.file.allow=always`. [7](#0-6) 

### Impact Explanation
If `allowFileProtocol=true` is reachable from a checkout of an untrusted/attacker-authored branch or commit (e.g., checking out a branch from a fork/PR, or a commit supplied via "Open in Desktop" / deep-link flows that call `checkoutBranch`/`checkoutCommit`), a malicious `.gitmodules` file can declare a submodule URL such as `file:///Users/victim/some-other-local-repo`. Git would then locally "clone" that directory into the submodule path, exposing its history/files inside the attacker-authored repository's working tree and potentially exfiltrating it on the next push, or landing arbitrary local content (including hook scripts) into a location the user will subsequently browse/open/build. This corresponds to the report's broken invariant: an unprivileged, "helpful" upgrade/override mechanism (`protocol.file.allow=always`, analogous to `updateSolution`'s unrestricted upgrade path) removes a safety boundary that was specifically added to stop untrusted input (submodule config) from reaching sensitive local resources.

### Likelihood Explanation
Exploitability depends entirely on whether any caller in the app sets `allowFileProtocol=true` for checkouts of content the user does not already trust (I could not fully trace every call site of `checkoutBranch`/`checkoutCommit` with this argument set to `true` within the available index — the `checkout-test.ts` fixture demonstrates the flag being used to auto-initialize a previously-uninitialized submodule during a branch checkout, which is a normal desktop workflow, not necessarily attacker-triggered). This is the main uncertainty: if `allowFileProtocol` is only ever `true` for repositories the user already owns/trusts, the practical exploitability is low; if it is set `true` generically for any branch checkout (as suggested by the test using it without any repository-trust distinction), it is a live problem for any repo containing a hostile fork/branch.

### Recommendation
Do not blanket-enable `protocol.file.allow=always` for submodule updates triggered by checking out branches/commits that originate from a remote/untrusted source. Restrict `file://` submodule allowance to submodules whose URL resolves to a path already tracked/owned by the current repository session, or drop the override entirely and let Git's default (`user`) reject unexpected `file://` submodule URLs, surfacing a clear error/prompt to the user instead of silently permitting the local clone.

### Proof of Concept
1. Attacker creates a public repository with a branch whose `.gitmodules` contains:
   ```
   [submodule "steal"]
     path = steal
     url = file:///Users/victim/.ssh
   ```
   (or any predictable/sensitive local path on the victim's machine).
2. Victim uses GitHub Desktop to check out that branch/commit via a code path that invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true` (as exercised in `app/test/unit/git/checkout-test.ts:150-189` for "initializes an uninitialized submodule when checking out a branch").
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, and Git clones the local `file://` path into `steal/` inside the victim's working tree.
4. The contents of the victim's local directory are now embedded in the attacker's tracked repository working copy, and can be committed/pushed by an unwitting subsequent action, or browsed/executed by the victim believing it is part of the original repository.

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
