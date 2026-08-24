## Title
`createMergeCommit` does not use hook interception (`interceptHooks`), leaking the Git-credential trampoline token to attacker-controlled repository hooks - (File: `app/src/lib/git/commit.ts`)

### Summary
This is the Desktop analog of the reported bug class: a state-syncing safeguard is applied consistently by one code path but is silently skipped by a sibling code path that performs an equivalent operation. In the Popcorn report, `Vault.withdraw` applies `syncFeeCheckpoint` while the near-identical `Vault.redeem` does not, corrupting `highWaterMark`. In GitHub Desktop, `createCommit` wraps its `git commit` invocation with the `interceptHooks` safeguard, while the structurally identical `createMergeCommit` (used to finish a merge/conflict resolution) omits it entirely.

### Finding Description
`createCommit` passes an explicit `interceptHooks` list (`pre-commit`, `prepare-commit-msg`, `commit-msg`, `post-commit`, …) to `git()`: [1](#0-0) 

`createMergeCommit`, which also runs `git commit` and therefore also triggers `pre-commit`/`post-commit` hooks, calls `git()` with no `interceptHooks` option at all: [2](#0-1) 

The `interceptHooks` mechanism matters because it changes *how* hooks are executed. When set, `withHooksEnv` redirects `core.hooksPath` to a proxy directory and hooks are actually run out-of-process by `createHooksProxy`, which builds a *safe, allow-listed* environment for the hook — only `GIT_`/`GITHEAD_`-prefixed variables, explicitly excluding `GIT_ASKPASS`, `GIT_SSH_COMMAND`, and `GIT_CONFIG_PARAMETERS`: [3](#0-2) [4](#0-3) 

When `interceptHooks` is *not* provided, `withHooksEnv` is a no-op and simply invokes the callback with the caller's raw `opts.env`: [5](#0-4) 

In that case, hooks are executed natively by the real `git` binary as children of the Desktop-spawned git process, inheriting the *entire* process environment set by `git()` in `core.ts` — including the trampoline credentials-broker variables `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN`, plus `GIT_CONFIG_PARAMETERS='credential.helper=desktop'`: [6](#0-5) [7](#0-6) 

`DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` are the authentication credentials Desktop's askpass/credential-helper trampoline uses to authorize requests from the spawned git process back to Desktop's internal trampoline server for retrieving the user's actual stored GitHub/SSH credentials. These are specifically excluded from the safe-env allow-list used by the hooks proxy (`safePrefixes = ['GIT_', 'GITHEAD_']`, and `GIT_CONFIG_PARAMETERS` is explicitly excluded) precisely because leaking them to a hook script is dangerous — but that protection only applies on the `interceptHooks` path.

### Impact Explanation
A cloned/fetched repository fully controls its own `.git/hooks/pre-commit` and `.git/hooks/post-commit` scripts. When a user resolves a merge conflict in Desktop and commits via `createMergeCommit`, that hook runs with the full Desktop process environment, including `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT`. A malicious hook can read these variables and connect to Desktop's local trampoline server (`trampolineServer`, reachable at `127.0.0.1:DESKTOP_PORT`) using the leaked token to impersonate a legitimate askpass/credential-helper request, obtaining the user's GitHub credentials/PAT or SSH key material for the current operation — i.e., credential/token exfiltration driven entirely by an attacker-controlled repository, matching the specified valid-impact category.

### Likelihood Explanation
Every merge-conflict resolution and commit flow in Desktop calls `createMergeCommit`, so the vulnerable path is hit through completely ordinary, expected user action (resolve conflicts → commit) on any repository that ships a hook file — no unusual steps, no local/admin access, and no prior compromise are required beyond cloning/opening the malicious repository, which is explicitly the intended attacker model.

### Recommendation
Pass the same `interceptHooks` (and associated `onHookProgress`/`onHookFailure`) options used by `createCommit` to the `git()` call inside `createMergeCommit`, so `pre-commit`/`post-commit` hooks triggered while finishing a merge go through the sandboxed hooks-proxy with its allow-listed, trampoline-token-free environment, consistent with the rest of the commit-creation code paths.

### Proof of Concept
1. Clone/open a repository containing `.git/hooks/post-commit` (or `pre-commit`) that reads `process.env.DESKTOP_TRAMPOLINE_TOKEN` and `process.env.DESKTOP_PORT` and forwards them to an attacker server, or uses them directly to query `127.0.0.1:$DESKTOP_PORT` as an askpass/credential-helper client.
2. Create a merge conflict (e.g., merge two diverging branches) and resolve it in Desktop, then click “Commit merge,” which invokes `createMergeCommit`.
3. Because `createMergeCommit` never sets `interceptHooks`, `withHooksEnv` is a no-op, so the `post-commit` hook runs as a normal child of Desktop's `git commit` process and inherits `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` set by `withTrampolineEnv`.
4. Using the leaked token, the hook (or a process it spawns) contacts Desktop's trampoline server before it expires and retrieves the credentials Desktop would have provided to `git`, exfiltrating them outside the repository/sandbox — something the `interceptHooks` guard in `createCommit` specifically prevents.

### Citations

**File:** app/src/lib/git/commit.ts (L51-70)
```typescript
  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
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
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
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

**File:** app/src/lib/hooks/hooks-proxy.ts (L166-176)
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

**File:** app/src/lib/git/core.ts (L276-296)
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
          ).catch(err => {
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L121-147)
```typescript
    // operation if there was one pending to be stored.
    try {
      return await fn({
        DESKTOP_PORT: await trampolineServer.getPort(),
        DESKTOP_TRAMPOLINE_TOKEN: token,
        GIT_ASKPASS: '',
        // This warrants some explanation. We're configuring the
        // credential helper using environment variables rather than
        // arguments (i.e. -c credential.helper=) because we want commands
        // invoked by filters (i.e. Git LFS) to be able to pick up our
        // configuration. Arguments passed to git commands are not passed
        // down to filters.
        //
        // We're using the undocumented GIT_CONFIG_PARAMETERS environment
        // variable over the documented GIT_CONFIG_{COUNT,KEY,VALUE} due
        // to an apparent bug either in a Windows Python runtime
        // dependency or in a Python project commonly used to manage hooks
        // which isn't able to handle the blank environment variables we
        // need when using GIT_CONFIG_*.
        //
        // See https://github.com/desktop/desktop/issues/18945
        // See https://github.com/git/git/blob/ed155187b429a/config.c#L664
        GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'credential.helper=' 'credential.helper=desktop'`,

        GIT_USER_AGENT: await GitUserAgent(),
        ...sshEnv,
      })
```
