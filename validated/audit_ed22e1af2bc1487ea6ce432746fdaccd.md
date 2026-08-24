## Title
GitHub Desktop explicitly disables Git's built-in clone-time hook/config protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - ([File: app/src/lib/git/clone.ts])

### Summary
The `BathBuddy` report is about a security guard (`Pausable`) that exists in the code but whose activation switch was never wired up, leaving admins unable to engage protection when needed. The equivalent broken invariant in this codebase is the opposite failure mode: Git upstream *does* provide an activation switch for a clone-time security guard, and GitHub Desktop's clone implementation explicitly flips it to the disabled state on every single clone, unconditionally, regardless of whether the source is a trusted GitHub remote or an attacker-supplied URL.

### Finding Description
`clone()` in [1](#0-0)  builds the execution environment for every `git clone` invocation and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`:

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
```

`GIT_CLONE_PROTECTION_ACTIVE` is the upstream Git safety switch that was added to guard against maliciously crafted repositories that can smuggle a `core.hooksPath`/hook configuration that gets picked up and executed during the clone/checkout that `git clone` performs internally (the class of issue fixed by Git's clone-time hook execution hardening). This is precisely the "pause" analog: Git ships the guard, but the caller — Desktop — actively disables it for every clone it performs, the same way `BathBuddy` inherits `Pausable`'s guard logic but never exposes a way to engage it. Here it's worse: Desktop doesn't just fail to *enable* the guard, it explicitly forces it *off*.

The clone command itself passes `--recursive` [2](#0-1) , meaning submodules (which are themselves attacker-controlled content from the remote being cloned) are fetched and checked out in the same operation with the protection disabled.

Other guards that exist elsewhere in this codebase do not cover this path:
- `interceptHooks`/`withHooksEnv`, the mechanism Desktop built to intercept and sandbox hook execution, is wired into `merge.ts`, `commit.ts`, `pull.ts`, and `push.ts` (confirmed via `interceptHooks` usage), but `clone.ts` never passes `interceptHooks` to its `IGitStringExecutionOptions`, so the hook-interception sandbox — Desktop's own equivalent "pause" mechanism for hooks — is not engaged during clone either. [3](#0-2) 
- The unsafe-directory ("dubious ownership") check surfaced via `addSafeDirectory`/`getRepositoryType` in `add-existing-repository.tsx` and `missing-repository.tsx` only protects *adding an existing local path*, not freshly cloned repositories under Desktop's control. [4](#0-3) 
- The `isClonePathSensitive` check in the same file only defends the destination path against traversal, not the hook-execution path during the clone/checkout itself. [5](#0-4) 

So two independent guards that should protect an unprompted clone from an attacker-controlled remote — Git's own clone protection and Desktop's own hook-interception sandbox — are both bypassed on the clone code path, and there is no compensating control.

### Impact Explanation
If the disabled Git-level clone protection is what stands between a benign clone and hook-triggered code execution (which is its documented purpose upstream), then cloning a malicious repository through Desktop's "Clone repository" UI, or via a deep link (`x-github-client://openRepo`)/`gh repo clone` style flow that routes through this same `clone()` function, could result in arbitrary hook code executing on the user's machine outside of any user-visible consent step, and outside Desktop's own hook-interception/progress-reporting sandbox. This matches the "code execution from a cloned/fetched repository" impact class explicitly called out as valid in scope.

### Likelihood Explanation
Every single clone operation performed through Desktop — including cloning a URL a user pastes or clicks from a webpage, a `x-github-client://` deep link, or any GitHub API-derived clone URL — goes through this exact code path with the protection forced off and no interception sandbox engaged, so the precondition ("attacker controls a cloned repository") is trivially satisfiable and requires no unusual user behavior beyond the normal "clone this repo" action the app is designed to invite.

### Recommendation
1. Do not unconditionally set `GIT_CLONE_PROTECTION_ACTIVE: 'false'`. Determine why it was disabled (likely to silence a false-positive/warning for legitimate clones) and replace the blanket bypass with a narrower fix, or route the warning through the existing "untrusted/unsafe repository" UI (as already done for `addSafeDirectory`) so the user makes an informed trust decision instead of Desktop silently pre-approving it.
2. Extend `interceptHooks` support to the `clone()` execution options so freshly cloned (and recursively cloned submodule) content is subject to the same hook-interception sandbox used for `merge`, `commit`, `pull`, and `push`.
3. Add regression tests analogous to `app/test/unit/git/clone-test.ts` and `app/test/unit/clone-path-safety-test.ts` that assert a crafted repository cannot get a hook executed unsandboxed during `clone()`.

### Proof of Concept
Exact reproduction requires a crafted upstream repository payload that exploits the specific hook/config-injection technique that `GIT_CLONE_PROTECTION_ACTIVE` was designed to stop (this precise payload construction is a property of the installed Git version's clone-time checkout/hook path, not of Desktop's code, and could not be verified against the sandboxed dugite Git binary from static analysis alone). What is verifiable from local code is the invariant break itself:
1. `clone()` is invoked by `AppStore._clone` for any user-entered/deep-linked URL.
2. `clone()` always merges `GIT_CLONE_PROTECTION_ACTIVE: 'false'` into the child process environment before calling `git(['...', 'clone', '--recursive', ...], ...)`. [6](#0-5) 
3. No call site passes `interceptHooks`, and `withHooksEnv` only activates when `opts?.interceptHooks` is set. [7](#0-6) 
4. Therefore any hook-execution vector normally caught by either guard reaches the underlying Git binary unguarded during every Desktop-initiated clone.

### Citations

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

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-42)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }

  const hooks = await Array.fromAsync(getRepoHooks(path, opts.interceptHooks))

  if (hooks.length === 0) {
    return fn(opts?.env)
  }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-157)
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
```
