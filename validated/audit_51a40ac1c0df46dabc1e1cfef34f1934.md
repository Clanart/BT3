This is the key finding: `checkoutBranch` and `checkoutCommit` in `app/src/lib/git/checkout.ts` invoke `git checkout` **without** `interceptHooks` set in their options, unlike `merge()` in `app/src/lib/git/merge.ts`, `commit.ts`, `pull.ts`, and `push.ts`, which all pass `interceptHooks: [...]` to route hook execution through the sandboxed `withHooksEnv`/`hooks-proxy` mechanism. [1](#0-0) [2](#0-1) 

### Title
Unsandboxed execution of repository-controlled `post-checkout` hooks during branch/commit checkout - (File: app/src/lib/git/checkout.ts)

### Summary
Git checkout operations trigger the `post-checkout` hook, but `checkoutBranch` and `checkoutCommit` do not pass `interceptHooks` to the underlying `git()` call, so the sandboxing/interception path in `app/src/lib/hooks/with-hooks-env.ts` and `app/src/lib/hooks/hooks-proxy.ts` is skipped for this operation. In contrast, `merge`, `pull`, `push`, and `commit` explicitly enumerate the hooks they intercept (`interceptHooks: ['pre-merge-commit', 'post-merge', 'commit-msg']`, etc.), which forces those hooks through the process-proxy sandbox that strips sensitive env vars (`GIT_ASKPASS`, `GIT_SSH_COMMAND`, `GIT_CONFIG_PARAMETERS`, etc.) and re-executes them in a controlled shell environment. [3](#0-2) [4](#0-3) 

### Finding Description
The bug-class in the external report is: a callback triggered mid-initialization by attacker-influenced content is allowed to run with the same trust level as the caller, corrupting state that later authorizes privileged actions. The Desktop analog is structural rather than a race: whenever a `.git/hooks/<hookname>` script exists (e.g. via a submodule, a previous `core.hooksPath` pointed at a repo-tracked directory, or any workflow that populates local hooks from repository content the user does not fully control), git will invoke it directly as a native process during `checkoutBranch`/`checkoutCommit`, because these code paths do not set `interceptHooks`. This bypasses the app's own hook-sandboxing invariant (`withHooksEnv`), which exists precisely to prevent unsandboxed native execution of repo-supplied hook scripts for other git operations (`merge`, `commit`, `pull`, `push`).

`getRepoHooks()` (used by `withHooksEnv`) resolves the hook directory via `core.hooksPath`, and it is that resolved hooks path, plus the hooks that already exist on disk, that are trusted/executed unsandboxed when `interceptHooks` is omitted, since the app falls straight through to `fn(opts?.env)`. [5](#0-4) 

### Impact Explanation
If a `post-checkout` hook is present (via submodule-provided hooks configuration, a compromised `core.hooksPath`, or any flow that leaves an executable hook file under the resolved hooks path), simply switching branches or checking out a commit via `checkoutBranch`/`checkoutCommit` executes that script as a plain child process with the user's full privileges and unfiltered environment — no stripping of `GIT_ASKPASS`, `GIT_SSH_COMMAND`, credential-related variables, etc., and no `GITHUB_DESKTOP=1` sandbox marker. This can lead to arbitrary code execution, credential/token exfiltration, or file writes outside the repository, which matches the accepted impact categories for this task (attacker-controlled cloned/fetched repository content leading to code execution or credential exfiltration).

### Likelihood Explanation
Likelihood is moderate: Desktop already gates `git clone` and does not itself version `.git/hooks`, so a freshly cloned malicious repo alone cannot supply a hook. However, any workflow that results in an executable file landing in the resolved hooks directory (e.g., users following a project's own hook-setup instructions, submodule-provided hook installers, or a previously-configured `core.hooksPath` that resolves inside the working tree) becomes attacker-controllable content that fires unsandboxed on the very common "checkout" action, since `checkoutBranch`/`checkoutCommit` are used pervasively (branch switching, PR checkout, etc.) and never opt into interception the way `merge`/`commit`/`pull`/`push` do.

### Recommendation
Add `interceptHooks: ['post-checkout']` (and any other applicable hook such as `pre-checkout` support if added later) to the git execution options passed by `checkoutBranch` and `checkoutCommit` in `app/src/lib/git/checkout.ts`, mirroring the pattern already used in `merge.ts`, `commit.ts`, `pull.ts`, and `push.ts`, so `post-checkout` is routed through `withHooksEnv`/`hooks-proxy` like all other hook-firing operations.

### Proof of Concept
1. In a test repository, set `core.hooksPath` to a directory the working tree controls (or otherwise place an executable `post-checkout` script in the resolved hooks directory).
2. Make the `post-checkout` script perform an observable side effect (e.g., write outside the repo, or read `GIT_ASKPASS`/credential env vars and exfiltrate them).
3. From Desktop, call `checkoutBranch`/`checkoutCommit` (`app/src/lib/git/checkout.ts:102-146` / `163-207`) on that repository.
4. Observe that the `post-checkout` script runs directly (unsandboxed, full env, no `interceptHooks`), unlike calling `merge()` on the same repo with a `post-merge` hook, which instead is captured by `hooks-proxy.ts` and re-executed with a filtered/safe environment. [6](#0-5)

### Citations

**File:** app/src/lib/git/checkout.ts (L102-124)
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
```

**File:** app/src/lib/git/checkout.ts (L163-185)
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
```

**File:** app/src/lib/git/merge.ts (L53-59)
```typescript
  const { exitCode, stdout } = await git(args, repository.path, 'merge', {
    expectedErrors: new Set([GitError.MergeConflicts]),
    interceptHooks: ['pre-merge-commit', 'post-merge', 'commit-msg'],
    onHookProgress: options?.onHookProgress,
    onHookFailure: options?.onHookFailure,
    onTerminalOutputAvailable,
  })
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L31-46)
```typescript
const excludedEnvVars: ReadonlySet<string> = new Set([
  // Dugite sets these, we don't want to leak them into the hook environment
  'GIT_SYSTEM_CONFIG',
  'GIT_EXEC_PATH',
  'GIT_TEMPLATE_DIR',
  // We set this to point to a custom hooks path which we don't want
  // leaking into the hook's environment. Initially I thought we would have
  // to sanitize this to strip out the custom config we set and leave any
  // user-configured but since we're executing the hook in a separate
  // shell with login it would just get re-initialized there anyway.
  'GIT_CONFIG_PARAMETERS',

  'GIT_ASKPASS',
  'GIT_SSH_COMMAND',
  'GIT_USER_AGENT',
])
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
