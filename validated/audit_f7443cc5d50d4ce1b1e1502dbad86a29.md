### Title
Silent, shell-level execution of repository-controlled Git hooks (default-enabled) — no per-repository consent gate — ([File: app/src/lib/hooks/with-hooks-env.ts])

### Summary
Foundry's `vm.ffi()` risk is that an opt-in feature lets code that ships inside a repository (a Solidity script) run arbitrary shell commands the moment a normal, expected workflow action (`forge test`) is performed. GitHub Desktop has a structurally identical primitive in its native Git-hooks support: once enabled, ordinary user actions (`commit`, `push`, `pull`, `merge`) transparently execute whatever executable scripts are discovered at `core.hooksPath`/`.git/hooks`, in the user's real shell environment, with no distinct per-repository warning comparable to the "unsafe directory / trust this repository" gate that Desktop already uses elsewhere for the same class of risk (auto-executing files found in a repository).

### Finding Description
`git()` in `app/src/lib/git/core.ts` unconditionally routes every Git invocation through `withHooksEnv()`: [1](#0-0) 

`withHooksEnv` (`app/src/lib/hooks/with-hooks-env.ts`) checks only a global, non-repository-specific toggle (`opts?.interceptHooks` set on `commit`, `push`, `pull`, `merge` operations, plus `getHooksEnvEnabled()`), then enumerates the executable hook files present in the repository and stages a proxy for each: [2](#0-1) 

Hook discovery (`getRepoHooks`) resolves the hooks directory from `core.hooksPath` (a Git config value) or the default `.git/hooks`, and yields any executable file matching a known hook name — with no check on the file's origin or trust level: [3](#0-2) 

When a hook fires, `hooks-proxy.ts` actually spawns Git's own `git hook run <hookName>` in the user's real shell environment (login shell env loaded via `getShellEnv`), not the app's restricted process: [4](#0-3) 

Whether this executes is controlled purely by two local booleans, defaulted **on**: [5](#0-4) , and the project's own changelog documents this being turned on for everyone: `"[New] Enable hooks support by default - #21590"` [6](#0-5) .

Contrast this with the analogous risk of "adding untrusted repositories may automatically execute files in the repository," which Desktop explicitly warns about and gates behind an affirmative "Trust Repository" action elsewhere in the app: [7](#0-6)  and [8](#0-7) . No equivalent per-repository consent/trust prompt exists anywhere in the hooks code path (`with-hooks-env.ts`, `get-repo-hooks.ts`, `hooks-proxy.ts`, `config.ts`) before a newly opened/cloned repository's hook scripts are executed the first time a commit/push/pull/merge is performed.

### Impact Explanation
`core.hooksPath` is a normal, well-documented Git feature many projects instruct contributors to configure (e.g. `git config core.hooksPath .githooks`) so that hook scripts live as ordinary tracked files inside the repository. An attacker who controls such a repository (or a fork/branch a victim is asked to work with) can ship a hook script under that path. The first time the victim performs the corresponding Git action in Desktop (a commit, push, pull, or merge — all completely ordinary actions, not "unnatural" steps), Desktop will silently discover and run that script under the user's actual shell/login environment (not the previously hooks-disabling sandbox behavior Desktop used to enforce via `-c core.hooksPath=` override). This yields arbitrary code execution on the victim's machine with the user's full privileges, credential/token exfiltration (shell env may include tokens, SSH agent access, etc.), and silent corruption of subsequent commits/pushes (e.g., a malicious `pre-commit`/`commit-msg` hook rewriting content before it's pushed).

### Likelihood Explanation
Moderate-to-high. The feature is enabled by default (`enableHooksByDefault`, changelog entry #21590), requires no special privileges from the attacker, and only requires the victim to follow a common, expected project-onboarding convention (setting `core.hooksPath` to a repo-tracked directory) or clone a repository someone else already configured this way in a shared clone. Unlike the "unsafe directory ownership" check that gates automatic execution risk for locally-owned-by-another-user repos, there is no equivalent trust gate keyed to "this repository's hooks have never been run before" — the only control is a single global on/off switch, not scoped per repository.

### Recommendation
Apply the same "trust this repository" UX pattern Desktop already uses for unsafe/untrusted directories (`add-existing-repository.tsx`, `missing-repository.tsx`) to hook execution: before running a repository's hooks for the first time (or whenever the hooks discovered under `core.hooksPath` change), prompt the user to review and explicitly approve running scripts from that specific repository, and persist that trust decision per-repository rather than as a single global toggle. Additionally, consider warning distinctly when `core.hooksPath` resolves to a path inside the tracked working tree (as opposed to the default `.git/hooks`, which is never populated by `clone`), since that is the case where the hook content is directly attacker-controlled via the repository contents.

### Proof of Concept
1. Attacker publishes a repository containing a `.githooks/pre-push` script (executable, containing an arbitrary payload) and a README instructing: `git config core.hooksPath .githooks` (a real, common pattern for teams avoiding Husky/managed hook installers).
2. Victim clones the repo with GitHub Desktop and, following the README, runs the one-time `git config core.hooksPath .githooks` command (via terminal or Desktop's own git-config UI) — a normal setup step, not an unusual or suspicious action.
3. Victim performs an ordinary `git push` from Desktop. `git()` → `withHooksEnv()` (`app/src/lib/hooks/with-hooks-env.ts:29-42`) finds the `pre-push` hook via `getRepoHooks()` (`app/src/lib/hooks/get-repo-hooks.ts:76-106`), stages the proxy, and `hooks-proxy.ts:245-264` spawns `git hook run pre-push`, which executes the attacker's `.githooks/pre-push` script in the victim's real shell environment — with no distinct confirmation dialog specific to this repository's hooks.
4. The attacker's script executes with the victim's OS-user privileges.

Note: I could not find, within the indexed portion of the codebase, any additional per-repository consent/warning UI specific to first-time hook execution (only the global preference toggle in `app/src/ui/preferences` and `app/src/lib/hooks/config.ts`); if such a gate exists elsewhere it was not discoverable via the available search tools, and a full Devin session with complete filesystem access could confirm this definitively.

### Citations

**File:** app/src/lib/git/core.ts (L276-294)
```typescript
  return withHooksEnv(
    hooksEnv =>
      withTrampolineEnv(
        async env => {
          const commandName = `${name}: git ${args.join(' ')}`

          const result = await GitPerf.measure(commandName, () =>
            exec(args, path, {
              ...opts,
              env: {
                // Explicitly set TERM to 'dumb' so that if Desktop was launched
                // from a terminal or if the system environment variables
                // have TERM set Git won't consider us as a smart terminal.
                // See https://github.com/git/git/blob/a7312d1a2/editor.c#L11-L15
                TERM: 'dumb',
                ...opts.env,
                ...hooksEnv,
                ...env,
              },
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

**File:** app/src/lib/hooks/hooks-proxy.ts (L245-264)
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
        .on('error', err => reject(err))

      // git-hook run takes care of ensuring we only get hook output on stderr
      // https://github.com/git/git/blob/4cf919bd7b946477798af5414a371b23fd68bf93/hook.c#L73C6-L73C22
      child.stderr.pipe(conn.stderr, { end: false }).on('error', reject)
      child.stderr.on('data', data => terminalOutput.push(data))
      conn.stdin.pipe(child.stdin).on('error', reject)
    })
```

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

**File:** app/src/ui/missing-repository.tsx (L111-134)
```typescript
    if (isPathUnsafe) {
      return (
        <UiView id="missing-repository-view">
          <div className="title-container">
            <div className="title">
              {this.props.repository.name} is potentially unsafe
            </div>
            <div className="details">
              <p>
                The Git repository at <Ref>{unsafePath}</Ref> appears to be
                owned by another user on your machine. Adding untrusted
                repositories may automatically execute files in the repository.
              </p>
              <p>
                If you trust the owner of the directory you can add an exception
                for this directory in order to continue.
              </p>
            </div>
          </div>

          <Row>{buttons}</Row>
        </UiView>
      )
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
