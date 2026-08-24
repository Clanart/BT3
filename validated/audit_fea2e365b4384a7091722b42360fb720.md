## Finding: GitHub Desktop explicitly disables Git's clone-time protection against malicious repository content

### Title
Desktop disables `GIT_CLONE_PROTECTION_ACTIVE` during recursive clone, re-enabling execution of attacker-controlled repository configuration/hooks - (File: `app/src/lib/git/clone.ts`)

### Summary
Desktop's `clone()` helper unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment passed to `git clone --recursive`. This environment variable is the safety switch Git itself introduced (as part of the fix for the CVE-2024-32002/32004/32020/32021 family of "clone leads to code execution" vulnerabilities) to detect and reject repositories — including nested submodules — that try to smuggle a symlinked `.git` directory, an alternate `core.hooksPath`, or other repository-controlled configuration that would let cloning a hostile repo execute attacker-supplied code or write files into unintended locations. By forcing this protection off for every clone, Desktop restores exactly the attack surface Git upstream closed off.

### Finding Description
In [1](#0-0) , the `clone()` function builds the environment for `git clone --recursive` and explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` alongside the caller-supplied remote URL. Immediately after, `updateSubmodulesAfterOperation` (invoked from `checkoutBranch`/`checkoutCommit` in [2](#0-1) ) can additionally pass `-c protocol.file.allow=always` (see [3](#0-2) ), further loosening submodule-fetch restrictions that upstream Git also tightened as defense-in-depth against malicious repository configuration.

The invariant that should hold is: content that originates entirely from an untrusted, attacker-controlled remote repository (the clone target and any of its recursively-fetched submodules) must never be able to direct Desktop/Git to write outside the intended clone directory or to configure Git in a way that leads to code execution merely by cloning. Git's own `GIT_CLONE_PROTECTION_ACTIVE` guard exists specifically to enforce that invariant for the recursive-submodule clone path, where a submodule can be pointed at a specially crafted nested repository containing a symlinked `.git`/hooks directory. Desktop's other hardening in this same file — `isClonePathSensitive()` — only guards against the *destination path itself* being a sensitive OS location (e.g., `~/.ssh`, `~/.gnupg`); it does nothing to constrain what a hostile submodule inside the cloned tree can do once Git's own protection has been switched off.

### Impact Explanation
An attacker who controls a repository (or just a submodule referenced by any repository a victim clones with Desktop, including via "Open in Desktop" deep links that trigger `openOrCloneRepository`) can craft the repository so that, absent `GIT_CLONE_PROTECTION_ACTIVE`, `git clone --recursive` follows the exact vector the upstream Git CVE fixes were designed to block — e.g., a submodule whose `.git` file/symlink resolves to a location that lets Git write/execute a hook, or a config injection through `core.hooksPath`, during the clone itself, without the user running any command inside the repository afterward. That satisfies "attacker controls a cloned/fetched repository ... resulting in code execution" from the accepted impact classes for this analysis.

### Likelihood Explanation
Every clone performed by Desktop (via the UI clone dialog, CLI `--cli-clone`, or an `x-github-client://openRepo/...` deep link handled by `openOrCloneRepository` in [4](#0-3) ) goes through this same `clone()` function and therefore always disables the protection — there is no opt-in/opt-out or trust prompt gating this behavior, unlike the explicit "unsafe/untrusted directory" prompt Desktop shows for locally-added repositories (`buildRepositoryUnsafeError` in [5](#0-4) ). The clone path receives no equivalent warning, so a victim cloning any attacker-supplied URL (including a shared "Clone in Desktop" link) is unconditionally exposed.

### Recommendation
Do not disable `GIT_CLONE_PROTECTION_ACTIVE`; let Git's own clone-protection remain active (the default), and only relax it, if ever necessary, for a narrowly scoped and clearly justified case. Likewise, avoid passing `-c protocol.file.allow=always` for submodule updates that originate from untrusted remotes. If disabling this protection was intended to work around a legitimate compatibility issue (e.g., self-referencing local test fixtures), gate it behind an explicit, narrowly-scoped condition rather than applying it to every clone unconditionally, and add a regression test asserting that cloning a repository containing a submodule crafted per the `GIT_CLONE_PROTECTION_ACTIVE` threat model is rejected.

### Proof of Concept
1. Attacker publishes a Git repository `evil/repo` containing a `.gitmodules` entry pointing at a nested submodule crafted so that, when fetched during `--recursive` clone, its `.git` entry resolves to a symlink/path that Git's `GIT_CLONE_PROTECTION_ACTIVE` check would normally reject (the same construction used to reproduce the upstream Git clone-protection CVEs).
2. Attacker sends the victim a normal clone URL or an `x-github-client://openRepo/https://github.com/evil/repo` deep link.
3. Victim clones via Desktop's UI or clicks the link; Desktop calls `clone()` in [6](#0-5) , which sets `GIT_CLONE_PROTECTION_ACTIVE=false` and runs `git clone --recursive -- <url> <path>`.
4. Because Git's built-in protection is disabled, the crafted submodule is processed as it would have been before the upstream Git fix, allowing the attacker-controlled write/execution to occur purely as a result of cloning — no further user action inside the repository is required.

### Citations

**File:** app/src/lib/git/clone.ts (L68-94)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-174)
```typescript
  private buildRepositoryUnsafeError() {
    const { repositoryUnsafePath, path } = this.state
    if (
      !this.state.path.length ||
      !this.state.showNonGitRepositoryWarning ||
      !this.state.isRepositoryUnsafe ||
      repositoryUnsafePath === undefined
    ) {
      return null
    }

    // Git for Windows will replace backslashes with slashes in the error
    // message so we'll do the same to not show "the repo at path c:/repo"
    // when the entered path is `c:\repo`.
    const convertedPath = __WIN32__ ? path.replaceAll('\\', '/') : path

    const displayedMessage = (
      <>
        <p>
          The Git repository
          {repositoryUnsafePath !== convertedPath && (
            <>
              {' at '}
              <Ref>{repositoryUnsafePath}</Ref>
            </>
          )}{' '}
          appears to be owned by another user on your machine. Adding untrusted
          repositories may automatically execute files in the repository.
        </p>
        <p>
          If you trust the owner of the directory you can
          <LinkButton onClick={this.onTrustDirectory}>
            {' '}
            add an exception for this directory
          </LinkButton>{' '}
          in order to continue.
        </p>
      </>
    )

    const screenReaderMessage = `The Git repository appears to be owned by another user on your machine.
      Adding untrusted repositories may automatically execute files in the repository.
      If you trust the owner of the directory you can add an exception for this directory in order to continue.`

    return { screenReaderMessage, displayedMessage }
  }
```
