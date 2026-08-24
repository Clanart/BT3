## Confirmed vulnerability

### Title
`createMergeCommit()` omits `interceptHooks`, letting `commit-msg`/`post-commit` (and `pre-commit`, etc.) hooks run unsandboxed with the full trampoline environment - (File: `app/src/lib/git/commit.ts`)

### Summary
Every other Desktop-initiated commit path (`createCommit`, `merge`, `pull`) explicitly passes an `interceptHooks` list so that `withHooksEnv` redirects `core.hooksPath` to a temporary directory whose "hooks" are just the `process-proxy` binary, which forwards execution through `createHooksProxy`. That proxy strips the environment down to a `safeEnv` allow-list (`GIT_*`/`GITHEAD_*`, minus `excludedEnvVars`) before spawning `git hook run`. `createMergeCommit`, used to finish a conflicted merge, calls `git(['commit', '--no-edit', '--cleanup=strip'], repository.path, 'createMergeCommit')` with **no fourth `options` argument at all**, so `opts?.interceptHooks` is `undefined`. [1](#0-0) 

### Finding Description
`withHooksEnv` explicitly short-circuits the sandboxing when `interceptHooks` isn't supplied:
```
if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
  return fn(opts?.env)
}
``` [2](#0-1) 

Because `createMergeCommit` never sets `interceptHooks`, this check always takes the bypass branch: no `core.hooksPath` override is injected via `GIT_CONFIG_PARAMETERS`, and the git process runs with whatever `hooksEnv`/`env` it would have had otherwise — i.e. the real trampoline environment (`withTrampolineEnv`) merged straight into the child process's env in `core.ts`, rather than being filtered through `createHooksProxy`'s `safeEnv` allow-list. [3](#0-2)  Consequently, if `.git/hooks/commit-msg`, `pre-commit`, or `post-commit` exist in the merged repository (attacker-controlled content, since these hooks are typically not committed to the repo tree but can be pre-staged in a repository the user clones with a setup script, or set via `core.hooksPath` in a tracked config that Desktop reads), Git invokes them **directly from disk**, executing with the full, unfiltered Desktop process/trampoline environment instead of the sanitized proxy environment that every other commit path enforces. This breaks the safety invariant documented and enforced elsewhere in the codebase (`createCommit`, `merge`, `pull` all list `commit-msg`/`post-commit` etc. in `interceptHooks`). [4](#0-3) [5](#0-4) 

This is called from `AppStore._finishConflictedMerge`, which is the normal "finish a conflicted merge" flow reachable any time a user resolves conflicts from a repository that could contain such a hook (e.g. cloned from an attacker, or a conflict-triggering merge with a branch that ships hook files plus a mechanism to make them executable/registered, such as a checked-in `core.hooksPath` config pointing at a repo-tracked directory). [6](#0-5) 

### Impact Explanation
Hook scripts triggered this way run with the real, unsandboxed environment rather than the restricted `safeEnv`, so a malicious `commit-msg`/`pre-commit`/`post-commit` hook shipped in (or pointed to by `core.hooksPath` in) a cloned repository gets broader environment access than intended by the hooks-sandboxing design (e.g. trampoline/credential-related env vars that `excludedEnvVars` would otherwise strip in the proxy path). This is a "silent corruption of the commit safety invariant" class issue rather than a new arbitrary-write primitive, since a plain hooks-enabled non-sandboxed Desktop (an option that exists via `getHooksEnvEnabled`) would already run hooks with the real environment by design — the bug here is that this single commit path silently degrades to that unsandboxed behavior even when the user has hooks sandboxing enabled.

### Likelihood Explanation
Reaching this path requires only: (1) hooks sandboxing enabled (default per `enableHooksByDefault`) and (2) the user completing a merge with conflicts against a repository that contains executable hook files under `.git/hooks` or a repo-controlled `core.hooksPath` — both attacker-repo-controlled conditions consistent with the bounty's threat model (malicious cloned/fetched repository content, no local/admin access needed).

### Recommendation
Pass the same `interceptHooks` list used by `merge`/`createCommit` (`pre-commit`, `prepare-commit-msg`, `commit-msg`, `post-commit`, `pre-auto-gc`, etc.) to the `git(...)` call inside `createMergeCommit`, along with forwarding `onHookProgress`/`onHookFailure`/`onTerminalOutputAvailable` options so this commit path is sandboxed consistently with all others.

### Proof of Concept
1. Prepare a repository with two branches that conflict on merge.
2. In that repository's `.git/hooks/commit-msg` (or via a tracked `core.hooksPath` pointing at a repo-controlled directory), place an executable script that exfiltrates `process.env` or writes to disk outside the repo.
3. Open the repo in Desktop, trigger a merge that produces conflicts, resolve conflicts, and click "Continue merge" — this calls `dispatcher.finishConflictedMerge` → `AppStore._finishConflictedMerge` → `createMergeCommit`.
4. Observe that the hook executes directly (not proxied through `process-proxy`/`git hook run`) and receives the full environment, unlike the equivalent normal-commit or merge-without-conflicts flows where the hook only sees the `safeEnv` subset via `createHooksProxy`. [7](#0-6)

### Citations

**File:** app/src/lib/git/commit.ts (L56-65)
```typescript
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
```

**File:** app/src/lib/git/commit.ts (L102-134)
```typescript
  const result = await git(
    [
      'commit',
      // no-edit here ensures the app does not accidentally invoke the user's editor
      '--no-edit',
      // By default Git merge commits do not contain any commentary (which
      // are lines prefixed with `#`). This works because the Git CLI will
      // prompt the user to edit the file in `.git/COMMIT_MSG` before
      // committing, and then it will run `--cleanup=strip`.
      //
      // This clashes with our use of `--no-edit` above as Git will now change
      // it's behavior to invoke `--cleanup=whitespace` as it did not ask
      // the user to edit the COMMIT_MSG as part of creating a commit.
      //
      // From the docs on git-commit (https://git-scm.com/docs/git-commit) I'll
      // quote the relevant section:
      // --cleanup=<mode>
      //     strip
      //        Strip leading and trailing empty lines, trailing whitespace,
      //        commentary and collapse consecutive empty lines.
      //     whitespace
      //        Same as `strip` except #commentary is not removed.
      //     default
      //        Same as `strip` if the message is to be edited. Otherwise `whitespace`.
      //
      // We should emulate the behavior in this situation because we don't
      // let the user view or change the commit message before making the
      // commit.
      '--cleanup=strip',
    ],
    repository.path,
    'createMergeCommit'
  )
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

**File:** app/src/lib/git/core.ts (L276-295)
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
            })
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

**File:** app/src/lib/stores/app-store.ts (L7536-7563)
```typescript
  public async _finishConflictedMerge(
    repository: Repository,
    workingDirectory: WorkingDirectoryStatus,
    manualResolutions: Map<string, ManualConflictResolution>
  ): Promise<string | undefined> {
    /**
     *  The assumption made here is that all other files that were part of this merge
     *  have already been staged by git automatically (or manually by the user via CLI).
     *  When the user executes a merge and there are conflicts,
     *  git stages all files that are part of the merge that _don't_ have conflicts
     *  This means that we only need to stage the conflicted files
     *  (whether they are manual or markered) to get all changes related to
     *  this merge staged. This also means that any uncommitted changes in the index
     *  that were in place before the merge was started will _not_ be included, unless
     *  the user stages them manually via CLI.
     *
     *  Its also worth noting this method only used in the Merge Conflicts dialog flow, not committing a conflicted merge via the "Changes" pane.
     *
     *  *TLDR we only stage conflicts here because git will have already staged the rest of the changes related to this merge.*
     */
    const conflictedFiles = workingDirectory.files.filter(f => {
      return f.status.kind === AppFileStatusKind.Conflicted
    })
    const gitStore = this.gitStoreCache.get(repository)
    return await gitStore.performFailableOperation(() =>
      createMergeCommit(repository, conflictedFiles, manualResolutions)
    )
  }
```

**File:** app/src/lib/hooks/hooks-proxy.ts (L166-177)
```typescript
    // GIT_ vars are considered safe to pass to hooks unless explicitly excluded
    // GITHEAD_ are set by git-merge (https://github.com/git/git/blob/83a69f19359e6d9bc980563caca38b2b5729808c/builtin/merge.c#L1590)
    const safePrefixes = ['GIT_', 'GITHEAD_']

    const safeEnv = Object.fromEntries(
      Object.entries(proxyEnv).filter(
        ([k]) =>
          safePrefixes.some(prefix => k.startsWith(prefix)) &&
          !excludedEnvVars.has(k)
      )
    )

```
