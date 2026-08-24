## Finding

Based on the investigation, the closest real analog to "lack of pause functionality" in GitHub Desktop is the **default-on git hook execution pipeline**, which has no pause/confirmation gate distinct from (and not covered by) the existing "unsafe repository" ownership warning.

### Title
Git hooks execute automatically in the user's real shell environment with no trust/pause gate for repositories that pass the ownership check - (File: `app/src/lib/hooks/with-hooks-env.ts`, `app/src/lib/hooks/get-repo-hooks.ts`, `app/src/lib/hooks/hooks-proxy.ts`)

### Summary
Desktop now runs Git hooks (`pre-commit`, `post-checkout`, `post-merge`, `pre-push`, etc.) by default, in the user's actual shell environment (loaded from `.bashrc`/`.zshrc`/PowerShell profile), whenever a repository's hooks directory contains an executable file matching a known hook name. This is enabled unconditionally by `getHooksEnvEnabled()`, which defaults to `true` as of the "Enable hooks support by default" change. [1](#0-0) [2](#0-1) 

### Finding Description
`withHooksEnv` is invoked from every hook-eligible git operation (`commit`, `merge`, `pull`, `push`) whenever `opts.interceptHooks` and `getHooksEnvEnabled()` are true. [3](#0-2) 

It calls `getRepoHooks`, which enumerates any executable file in the repository's hooks directory (`core.hooksPath` or `.git/hooks`) whose name matches Git's known hook list — it does **not** check whether the repository, its content, or its ownership is trusted beyond the pre-existing "dubious ownership" check used elsewhere in the UI (`add-existing-repository.tsx`, `missing-repository.tsx`). [4](#0-3) [5](#0-4) 

That ownership check only fires when a directory is owned by a *different* user on the machine. It does nothing to guard a directory the current user just extracted from a downloaded archive, copied from removable media, or otherwise obtained non-interactively — such a directory is owned by the current user and will never trip the "unsafe" warning, yet it can arrive with a fully populated `.git/hooks/*` (or a `core.hooksPath`-referenced) directory containing attacker-authored, already-executable scripts.

Once a hook file is discovered, `createHooksProxy` spawns it directly using the shell environment obtained via `getShellEnv`, which loads the user's *actual* interactive-shell environment (Git Bash/PowerShell/cmd profile) rather than a minimal sandboxed one, and tags the process with `GITHUB_DESKTOP: '1'`. [6](#0-5) 

The only mechanism resembling a "pause" is an `AbortController` surfaced through the `onHookProgress` callback, but this is a reactive best-effort cancel path for a hook that has *already started running* — there is no gate that pauses/holds hook discovery-and-execution before the first invocation to let the user review or approve what will run, the way the ownership-based "Trust Repository" flow does for the (narrower) dubious-ownership case. [7](#0-6) 

### Impact Explanation
If a user opens (not necessarily "adds as unsafe") a repository that already contains executable hook scripts under `.git/hooks` or a `core.hooksPath`-pointed directory — e.g., obtained via a zip/tarball download, copied from a USB drive, or a repository directory received via any non-`git clone` channel — the very next ordinary action (commit, merge, pull, push) silently executes attacker-controlled code in the user's real, fully-privileged shell environment, with no confirmation step and no way to hold/pause execution before it starts. This is a meaningfully broader execution surface than raw Git's own hook execution because Desktop explicitly re-hydrates the user's login-shell environment for the hook process.

### Likelihood Explanation
Requires the victim to open/add a repository whose working tree/hooks directory was populated by means other than a normal `git clone` (which does not transfer hooks) — e.g., unzip'd repo, copied `.git` folder, or a repository restored from backup/CI artifact. This is a plausible but not universal user action; ownership will not flag it as unsafe because the user is the one who materialized the files.

### Recommendation
Gate first-time hook execution per repository behind an explicit user confirmation ("This repository has custom Git hooks — allow Desktop to run them?"), independent of and in addition to the existing dubious-ownership check, and surface the existing `AbortController`/`onHookProgress` abort path as an actual "Cancel hook" UI control before/while a hook first runs for a given repository.

### Proof of Concept
1. Attacker creates a normal-looking Git repo, adds a `.git/hooks/post-checkout` (or `pre-commit`) script with the executable bit set, containing arbitrary shell commands, and ships it as a `.zip`/tarball (e.g., "sample project," "vendored dependency," email attachment).
2. Victim downloads and extracts the archive locally (files are now owned by the victim → ownership check never triggers) and adds the folder as an existing repository in Desktop.
3. Victim performs a routine action that triggers a matching hook (e.g., checking out a branch, committing).
4. `getRepoHooks` finds the executable hook file, and `createHooksProxy`/`hooks-proxy.ts` executes it directly in the victim's real shell environment with no prior confirmation dialog — confirmed by the code path in `with-hooks-env.ts` → `get-repo-hooks.ts` → `hooks-proxy.ts` cited above.

**Uncertainty note:** I was not able to fully trace, within the available iterations, whether `app-store.ts`'s consumption of `onHookProgress`/`onHookFailure` exposes any additional UI-level confirmation or cancel affordance before a hook's first execution; if such a gate exists there, it would partially mitigate this finding. This should be verified directly in `app/src/lib/stores/app-store.ts` before treating this as fully confirmed.

### Citations

**File:** app/src/lib/hooks/config.ts (L1-12)
```typescript
import { enableHooksByDefault, enableHooksEnvironment } from '../feature-flag'
import { getBoolean, setBoolean } from '../local-storage'

export const defaultHooksEnvEnabledValue = enableHooksByDefault()

/**
 * Whether the hooks environment is enabled, takes into account the
 * `enableHooksEnvironment` feature flag.
 */
export const getHooksEnvEnabled = () =>
  enableHooksEnvironment() &&
  getBoolean('git-hooks-env-enabled', defaultHooksEnvEnabledValue)
```

**File:** changelog.json (L242-245)
```json
    "3.5.5-beta4": [
      "[New] Enable hooks support by default - #21590",
      "[Added] Support rebase hooks - #21586"
    ],
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

**File:** app/src/lib/hooks/get-repo-hooks.ts (L76-106)
```typescript
export async function* getRepoHooks(path: string, filter?: string[]) {
  const hooksPath = await getConfigValue(path, 'core.hooksPath')
    .catch(() => getHooksPath(path))
    .then(p => resolve(path, p))

  const files = await readdir(hooksPath, { withFileTypes: true })
    .then(entries => entries.filter(x => x.isFile()))
    .catch(() => [])

  const matchAll = filter?.includes('*')

  for (const file of files) {
    const hookName = basename(file.name, '.exe')

    if (matchAll || filter?.includes(hookName) === false) {
      continue
    }

    if (!knownHooks.includes(hookName)) {
      continue
    }

    if (__WIN32__) {
      // On Windows we have to assume that any valid hook name is executable
      // because the executable bit is not used there. Git looks for a shebang
      // but that seems expensive to check here :shrug:
      yield hookName
    } else if (await isExecutable(join(file.parentPath, file.name))) {
      yield hookName
    }
  }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-167)
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
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L158-164)
```typescript

    const abortController = new AbortController()
    const abort = () => abortController.abort()
    conn.on('close', abort)

    await writeline(conn.stderr, `Running ${hookName} hook...`)
    onHookProgress?.({ hookName, status: 'started', abort })
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L245-256)
```typescript
    const { code, signal } = await new Promise<{
      code: number | null
      signal: NodeJS.Signals | null
    }>((resolve, reject) => {
      const child = spawn(gitPath, args, {
        cwd: proxyCwd,
        // GITHUB_DESKTOP lets hooks know they're run from GitHub Desktop.
        // See https://github.com/desktop/desktop/issues/19001
        env: { ...shellEnv.env, ...safeEnv, GITHUB_DESKTOP: '1' },
        signal: abortController.signal,
      })
        .on('close', (code, signal) => resolve({ code, signal }))
```
