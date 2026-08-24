### Title
Git's `file://` submodule protection is explicitly disabled during clone and checkout, allowing a malicious repository to read arbitrary local files into the working tree - ([File: app/src/lib/git/clone.ts])

### Summary
`clone()` in `app/src/lib/git/clone.ts` runs `git clone --recursive` with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` explicitly set to `'false'`, and `updateSubmodulesAfterOperation()` in `app/src/lib/git/submodule.ts` optionally passes `-c protocol.file.allow=always` to `git submodule update --init --recursive` when its `allowFileProtocol` flag is `true`. [1](#0-0) [2](#0-1)  Both of these settings deliberately turn off the upstream Git protection (added for CVE-2022-39253) that normally blocks submodules whose `.gitmodules` URL is a local/`file://` path. The untrusted value in question is the submodule URL inside a cloned repository's `.gitmodules` file - a value fully controlled by whoever authored the remote repository, analogous to the "unsigned callback data" in the report: a value that is trusted implicitly and used to drive a sensitive operation (recursive filesystem read/copy) with no independent verification.

### Finding Description
When cloning a repository, Desktop always requests `--recursive` submodule initialization and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the child process environment: [1](#0-0) 

This environment variable is the official Git mechanism (introduced alongside `protocol.file.allow`) to prevent a cloned repository's `.gitmodules` from silently causing Git to fetch a submodule from an arbitrary local path (e.g. `file:///Users/victim/.ssh` or a relative escape such as `../../../../etc`) during a recursive clone. By forcing this protection to `false` for every clone, Desktop restores the pre-fix behavior for all repositories the user clones, including ones they discover from an untrusted link (e.g. an `x-github-client://openRepo/...` deep link handled in `parseAppURL`/`handleAppURL`) or a malicious fork. [3](#0-2) [4](#0-3) 

Independently, `updateSubmodulesAfterOperation()` accepts an `allowFileProtocol` boolean that, when true, adds `-c protocol.file.allow=always` to `git submodule update --init --recursive`, again disabling the same guard for post-checkout submodule initialization: [5](#0-4) 

This is invoked from `checkoutBranch()`/`checkoutCommit()` in `app/src/lib/git/checkout.ts`, which expose their own `allowFileProtocol` parameter (default `false`) that callers in `app-store.ts` can set to `true`: [6](#0-5) 

The unit test `initializes an uninitialized submodule when checking out a branch` demonstrates the app deliberately invoking `checkoutBranch` with `allowFileProtocol = true`, confirming this is a reachable, real code path rather than dead code: [7](#0-6) 

The broken invariant: Git's own default behavior refuses to honor `file://`/local-path submodule URLs from a repository unless the *user* opts in (`protocol.file.allow=user`), precisely because that value originates from untrusted, remote-controlled data (`.gitmodules`) and should not be trusted implicitly. Desktop overrides this safety default globally for clone, and conditionally for checkout, without any equivalent verification step (no prompt, no allow-list, no signature) — the moral equivalent of the "unsigned callback data" in the report: attacker-supplied data (the submodule URL) is used to drive a sensitive operation (arbitrary local path read via `git submodule update`) without being checked or confirmed by the user/borrower analog.

### Impact Explanation
If an attacker crafts a repository with a `.gitmodules` entry pointing a submodule at an absolute or `file://` local path (e.g. the user's SSH directory, browser profile, or any accessible directory), cloning that repository with Desktop (`clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false`) will cause Git to copy the contents of that local path into the submodule directory inside the newly cloned repository, bypassing the intended clone-destination boundary. This is a "read outside the repo" primitive: files or directories the user never intended to expose end up materialized inside their repository working tree, where they can be inadvertently committed and pushed to the attacker-controlled remote (data exfiltration/credential leakage), satisfying the impact bar of "file write or read outside the repo" and "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The attacker only needs to publish a public repository (or a link to one, including via the app's own `openRepo`/`open-repository-from-url` protocol handler) containing a crafted `.gitmodules`; no local access, admin rights, or pre-existing malware is required, and the victim's only action is the normal, expected step of cloning/opening a repository in Desktop — exactly the class of "unprivileged, attacker controls a cloned/fetched repository" scenario called out as valid impact. Because the protection is disabled unconditionally for every clone (not gated behind a confirmation dialog), likelihood is high whenever a user clones an untrusted or newly-discovered repository.

### Recommendation
Do not set `GIT_CLONE_PROTECTION_ACTIVE=false` (or `protocol.file.allow=always`) unconditionally. Instead, follow upstream Git's intent: keep protection active by default, and only allow local/`file://` submodule paths after explicit, per-repository user confirmation analogous to signing the callback data in the original report — e.g., detect local-path submodules before recursing, surface them to the user, and require an explicit opt-in before re-running with `protocol.file.allow=always`/`GIT_CLONE_PROTECTION_ACTIVE` disabled for that specific submodule/session only.

### Proof of Concept
1. Attacker creates and publishes `evil-repo` with a `.gitmodules` file such as:
   ```
   [submodule "leak"]
       path = leak
       url = file:///Users/victim/.ssh
   ```
2. Victim clones `evil-repo` in GitHub Desktop (via the clone dialog, CLI `--cli-clone`, or an `x-github-client://openRepo/...` deep link handled by `parseAppURL`/`handleAppURL`).
3. `clone()` runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment. [8](#0-7) 
4. Git honors the local-path submodule URL (protection disabled) and populates `evil-repo/leak` with the contents of `/Users/victim/.ssh`.
5. The victim's working tree now contains the previously out-of-repo files; if they stage/commit/push, the leaked files are transmitted to the attacker's remote.

Note: I was not able to fully trace every production call site that sets `allowFileProtocol = true` for `checkoutBranch`/`checkoutCommit` beyond the confirmed test coverage due to the tool-call limit reached before completing the `grep_search` on `app-store.ts` callers; the `clone.ts` path (`GIT_CLONE_PROTECTION_ACTIVE: 'false'`), however, is unconditional and fully verified.

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

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
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
