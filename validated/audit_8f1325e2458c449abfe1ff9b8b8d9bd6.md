### Title
Git hooks execute automatically with full user-shell privileges when opening/committing in an untrusted cloned repository - (File: `app/src/lib/hooks/with-hooks-env.ts`)

### Summary
The reported Gearbox issue is a "trust boundary" bug class: a single component (`Configurator`) is granted broad, unchecked authority over a sensitive resource (user funds) with no independent gate on that power. The closest analog in GitHub Desktop is the Git-hooks execution subsystem: once hook interception is enabled, Desktop will automatically discover and run *any* executable hook found in a repository's `.git/hooks` (or `core.hooksPath`) directory for common actions (commit, push, pull, merge, rebase), running that hook inside the **user's real login shell environment** (PATH, shell rc files, aliases, secrets sourced by profile scripts) rather than a minimal, sandboxed one. The only existing gate — the "unsafe directory" (`safe.directory`) ownership check surfaced in `add-existing-repository.tsx` / `missing-repository.tsx` — protects against directories owned by a *different OS user*, not against a same-user-owned repository whose content (including hook scripts) is attacker-authored, e.g. supplied as a zipped `.git` folder, a repository dump, or any repo where hook files ship pre-marked executable.

### Finding Description
`withHooksEnv` in `app/src/lib/hooks/with-hooks-env.ts` intercepts designated git subcommands (`interceptHooks`, e.g. `pre-push`, and per the changelog also merge/pull/rebase/commit hooks) and installs a proxy hooks path (`core.hooksPath=<tmp>`), then executes the real hooks discovered by `getRepoHooks` (`app/src/lib/hooks/get-repo-hooks.ts`). [1](#0-0) 

`getRepoHooks` resolves `core.hooksPath` (or the default `.git/hooks`) *from the repository's own local config*, and yields any file in that directory whose name matches a known hook and (on non-Windows) is marked executable — with no signature, provenance, or content check: [2](#0-1) 

Each discovered hook is then run via `createHooksProxy` by spawning it inside the full shell environment obtained from `getShellEnv` (the user's actual configured shell — bash/zsh/pwsh/cmd — which loads `.bashrc`/`.zshrc`/profile scripts and inherits `PATH`), rather than a restricted environment: [3](#0-2) 

This "load git hook environment variables from shell" behavior is an intentional, user-visible feature (`app/src/ui/preferences/git.tsx`) and is gated by `enableHooksEnvironment` (currently hardcoded `true`) and `getHooksEnvEnabled`: [4](#0-3) [5](#0-4) 

The only trust gate that exists anywhere in the codebase for "should we run things found in this repository" is the `safe.directory` / "Trust Repository" prompt, which is scoped purely to file-system ownership mismatches, not to content trustworthiness: [6](#0-5) [7](#0-6) 

Because a hook's executable bit and location are properties of the working tree/`.git` directory delivered to the user (e.g. via a downloaded `.git` bundle, a repository archive, a tarball claiming to be a project, or a repo where the victim is the owner of the files after extraction), the ownership check passes trivially, and Desktop proceeds to auto-execute the attacker-authored hook the next time the user performs a routine operation (commit, push, pull, merge) — with the full power of their interactive shell environment, not the narrower environment git itself would normally use.

### Impact Explanation
An attacker who gets a victim to open/clone/extract a repository they control (a `.git` folder in a zip, a "starter template", a tarball with pre-existing hooks) can achieve arbitrary code execution on the victim's machine the moment the victim performs a normal, expected action inside GitHub Desktop (commit/push/pull/merge). Because the hook runs with the user's full shell environment (sourcing shell rc files and inheriting `PATH`), it can access anything reachable from that shell context — SSH agent sockets, cached credentials, environment-stored tokens, cloud CLI sessions — enabling credential/token exfiltration well beyond the repository sandbox, and can write/read files anywhere the user's OS account can, i.e., outside the repository. This matches the "attacker controls a cloned/fetched repository ... resulting in code execution ... credential/token exfiltration" impact class from the task's Valid Impact criteria.

### Likelihood Explanation
Requires no privileged/local access beyond what a normal Desktop user already has by design (open/clone an untrusted repo and perform a routine git action). It does not require social engineering beyond what's already assumed acceptable in the report's threat model (attacker-controlled repository content), and the ownership check that exists is provably insufficient because it only verifies *who owns the files*, not whether the *content is safe to execute*. The hooks-environment feature was recently made a first-class, increasingly default-enabled capability (per `changelog.json` entries "Enable hooks support by default", "Support merge/pull/pre-push/rebase hooks", "Run hooks in user's shell environment"), widening the set of everyday actions that trigger hook execution.

### Recommendation
Treat hook execution as a privileged action gated on an explicit, per-repository, content-aware trust decision — analogous to how the report recommends replacing a single all-powerful `Configurator` with a multi-party/DAO-gated process. Concretely:
- Do not auto-execute hooks discovered in a newly added/extracted/opened repository without an explicit one-time user confirmation that names the hook file(s) that will run and lets the user inspect them first.
- Decouple "safe.directory" (ownership) trust from "hooks execution" trust; require a distinct prompt/allow-list keyed on repository identity (e.g., remote URL + first-seen hash) before ever invoking `getRepoHooks`/`withHooksEnv` for a given repository.
- When the hooks-shell-environment feature is enabled, avoid sourcing full interactive shell profiles (which may expose secrets) by default; offer a minimal/sanitized environment unless the user opts in per repository.

### Proof of Concept
1. Attacker creates a benign-looking project and manually creates `<repo>/.git/hooks/post-checkout` (or `pre-push`, `post-merge`, etc.) containing a malicious script, and marks it executable, then packages the whole folder (including `.git`) as a zip/tarball or shares it as a folder (not necessarily via `git clone`, since `git clone` normally doesn't ship executable hooks from the origin — but a delivered `.git` directory, restored backup, or "vibe-coded" template with a setup script that `chmod +x`'s a hook will).
2. Victim downloads/extracts the archive so the files are now owned by the victim's OS account (passing the ownership/`safe.directory` check trivially) and opens it in GitHub Desktop via "Add Existing Repository."
3. Victim performs a normal commit or push. `withHooksEnv` detects the hook via `getRepoHooks`, proxies it through `createHooksProxy`, and executes it inside the victim's actual login shell environment. [8](#0-7) [9](#0-8) 
4. The hook script exfiltrates SSH keys/tokens found via the inherited shell environment or writes files outside the repository, with no additional prompt or warning distinct from ordinary commit/push UI.

**Note on completeness:** I was not able to fully verify, within the available index, the exact conditions under which a delivered (non-`git clone`) repository would have pre-existing executable hooks recognized by `getRepoHooks`, nor the current default value of `enableHooksByDefault`/`enableBetaFeatures` in the production release channel — these depend on files (`enableBetaFeatures` full body) not fully returned by search. If precise confirmation of default-enablement in stable builds is required, a Devin session with full repository access should be used to inspect `app/src/lib/feature-flag.ts` in its entirety and trace `getChannel()`/`enableBetaFeatures`.

### Citations

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

**File:** app/src/lib/hooks/get-repo-hooks.ts (L54-106)
```typescript
const getHooksPath = async (path: string) =>
  resolve(
    path,
    (await git(['rev-parse', '--git-path', 'hooks'], path)).replace(
      /\r?\n$/,
      ''
    )
  )

const getConfigValue = (path: string, key: string) =>
  git(['config', '-z', '--get', key], path).then(x => x.split('\0')[0])

/**
 * Returns the names of executable Git hooks found in the given repository.
 *
 * @param path   The file system path to the Git repository (root of working
 *               directory).
 * @param gitDir The path to the .git directory for this repository. Used as
 *               the default hooks location when core.hooksPath is not set.
 * @param filter An optional array of hook names to filter the results.
 *               Including '*' will return all hooks.
 */
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

**File:** app/src/lib/feature-flag.ts (L120-122)
```typescript
export const enableHooksEnvironment = () => true

export const enableHooksByDefault = enableBetaFeatures
```

**File:** app/src/lib/hooks/config.ts (L4-12)
```typescript
export const defaultHooksEnvEnabledValue = enableHooksByDefault()

/**
 * Whether the hooks environment is enabled, takes into account the
 * `enableHooksEnvironment` feature flag.
 */
export const getHooksEnvEnabled = () =>
  enableHooksEnvironment() &&
  getBoolean('git-hooks-env-enabled', defaultHooksEnvEnabledValue)
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
