### Title
Clone with `GIT_CLONE_PROTECTION_ACTIVE: 'false'` disables Git's embedded-repository/hook protection during `--recursive` clone - (File: app/src/lib/git/clone.ts)

### Summary
The Sherlock report's broken invariant is: two steps that are supposed to be gated/separated (submitting an allocation update, then having it applied) are instead executable back-to-back inside a single atomic operation, letting the attacker's own untrusted input influence a decision before any safety window/validation can intervene. The closest verifiable analog in this Electron app is in `clone()`, where Desktop clones an attacker-controlled remote with `--recursive` while explicitly disabling Git's own defense-in-depth mechanism that prevents newly-fetched, untrusted repository content from writing/activating hook files that execute in the same operation. [1](#0-0) 

### Finding Description
`clone()` builds the environment for the `git clone` invocation and explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` alongside `--recursive`: [1](#0-0) [2](#0-1) 

`GIT_CLONE_PROTECTION_ACTIVE` is the upstream Git safeguard (introduced after the class of clone/`.git`-hooks-execution vulnerabilities such as CVE-2024-32002) that refuses to clone repositories/submodules whose layout would allow content coming from the untrusted remote to land inside the local `.git` directory (e.g. via case-insensitive or symlink-based path collisions between a submodule's worktree and `.git/hooks`) and get executed as part of finishing the same clone. Desktop unconditionally turns this protection off for every clone, and additionally passes `--recursive`, meaning submodules referenced by the attacker's remote are fetched and checked out in the same invocation.

Crucially, this `clone` invocation is dispatched through the shared `git()`/`withHooksEnv()`/`withTrampolineEnv()` pipeline used for all git commands: [3](#0-2) 

`withHooksEnv()` only sandboxes hook execution (via a filtered-environment proxy) when the caller explicitly supplies `interceptHooks` for that operation: [4](#0-3) 

`clone()` does not pass `interceptHooks` at all, so `withHooksEnv` short-circuits (`return fn(opts?.env)`), meaning the raw environment — including `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `GIT_CONFIG_PARAMETERS='credential.helper=desktop'` set up by `withTrampolineEnv` — is inherited directly by the `git clone` child process and, by extension, by anything Git executes as part of resolving that clone (hooks/filters triggered by a maliciously crafted embedded `.git` layout that the disabled protection would otherwise have blocked): [5](#0-4) 

By contrast, operations that do route through the hooks proxy (`pull`, `push`, `commit`, `merge`) get their environment scrubbed to `GIT_`/`GITHEAD_`-prefixed variables only, explicitly excluding the trampoline/credential variables: [6](#0-5) [7](#0-6) 

`clone` has no such scrubbing and no such interception, and it deliberately disables Git's own protection against exactly this scenario.

### Impact Explanation
If Git's `GIT_CLONE_PROTECTION_ACTIVE` guard would have blocked a crafted repository/submodule layout from causing arbitrary code to run mid-clone, disabling it here removes that backstop for every Desktop clone. Combined with `--recursive` (which walks into attacker-supplied submodule URLs/paths) and the fact that clone is exempted from the hook-proxy sandboxing that every other hook-triggering git command receives, a successful bypass would execute arbitrary code with access to `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` in its environment — the same token the credential-helper trampoline uses to authorize handing out the signed-in user's GitHub/GHE credentials over the local TCP trampoline server. That is a path to credential exfiltration and/or arbitrary file write/code execution outside of normal Desktop-mediated git operations, entirely from cloning an attacker-controlled repository (no local access, no admin, no prior malware needed).

### Likelihood Explanation
This requires the user to clone (or open+fetch) a malicious repository through Desktop — a normal, expected user action Desktop explicitly supports (clone URL / clone via deep link / `x-github-client://openRepo`). The vulnerable configuration (`GIT_CLONE_PROTECTION_ACTIVE: 'false'` + `--recursive` + no `interceptHooks`) is unconditional and applies to every clone performed by the app, not an edge case. What is uncertain from static review alone is the exact current-Git-version exploitability of the underlying embedded-`.git`/case-collision technique (this depends on the bundled Git/Dugite version and OS filesystem semantics) — I could not confirm in the index whether the bundled Git version is otherwise patched against the specific technique the flag was designed to stop, so likelihood should be validated against the vendored Git/Dugite version.

### Recommendation
- Do not unconditionally set `GIT_CLONE_PROTECTION_ACTIVE: 'false'`; only disable it if there is a concrete, documented compatibility reason, and prefer leaving Git's built-in protection enabled by default.
- Route `clone()` through the same hook-interception/sandboxing path (`interceptHooks`) used by `pull`/`push`/`commit`, or at minimum ensure the trampoline/credential-related environment variables (`DESKTOP_TRAMPOLINE_TOKEN`, `DESKTOP_PORT`, `GIT_CONFIG_PARAMETERS`) are not present in the environment used for the initial clone step of an untrusted remote, and only re-enabled for the parts of the flow that legitimately require authentication.
- If `--recursive` submodule cloning must happen in the same call, ensure it happens after protection checks or in a follow-up step whose environment is scrubbed the same way `hooks-proxy.ts` scrubs it for other commands.

### Proof of Concept
Static-analysis based (not dynamically executed):
1. `clone()` is invoked with an attacker-controlled `url` (e.g. via `x-github-client://openRepo/<attacker-repo>` deep link handled by `parseAppURL`/`dispatchURLAction`, or the "Clone repository" UI). [8](#0-7) 
2. The clone environment sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and the command includes `--recursive`, so submodules referenced by the attacker's repository are fetched/checked out in the same operation, with Git's clone-time embedded-repo protection disabled. [1](#0-0) 
3. Because `clone()` passes no `interceptHooks`, `withHooksEnv` skips the hooks-proxy sandbox entirely and the `git clone` subprocess (and anything it spawns) receives the full trampoline environment, including `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT`/`GIT_CONFIG_PARAMETERS=credential.helper=desktop`. [4](#0-3) [9](#0-8) 
4. Any code execution achieved via the disabled protection (e.g., a crafted submodule/embedded `.git` path collision) would run with that inherited environment, allowing it to speak the trampoline credential-helper protocol back to Desktop's local TCP server (`vendor/desktop-trampoline/src/desktop-trampoline.c`) and request the signed-in user's stored GitHub credential. [10](#0-9) 

I was unable to fully verify, purely from the indexed source, whether the specific filesystem/case-collision technique that `GIT_CLONE_PROTECTION_ACTIVE` guards against is still exploitable against the exact vendored Git/Dugite version bundled with this build of Desktop — confirming that requires checking the vendored Git version/patches, which is outside what the code index exposes. A Devin session with full repo/dependency access would be needed to pin down the exact Git version and attempt a live PoC clone against a crafted malicious repository.

### Citations

**File:** app/src/lib/git/clone.ts (L68-93)
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

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
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

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-36)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L93-147)
```typescript
export async function withTrampolineEnv<T>(
  fn: (env: object) => Promise<T>,
  path: string,
  isBackgroundTask = false,
  customEnv?: Record<string, string | undefined>
): Promise<T> {
  const sshEnv = await getSSHEnvironment()

  return withTrampolineToken(async token => {
    isBackgroundTaskEnvironment.set(token, isBackgroundTask)
    trampolineEnvironmentPath.set(token, path)

    const existingGitEnvConfig =
      customEnv?.['GIT_CONFIG_PARAMETERS'] ??
      process.env['GIT_CONFIG_PARAMETERS'] ??
      ''

    const gitEnvConfigPrefix =
      existingGitEnvConfig.length > 0 ? `${existingGitEnvConfig} ` : ''

    // The code below assumes a few things in order to manage SSH key passphrases
    // correctly:
    // 1. `withTrampolineEnv` is only used in the functions `git` (core.ts)
    // 2. Those two functions always thrown an error when something went wrong,
    //    and just return a result when everything went fine.
    //
    // With those two premises in mind, we can safely assume that right after
    // `fn` has been invoked, we can store the SSH key passphrase for this git
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

**File:** vendor/desktop-trampoline/src/desktop-trampoline.c (L51-118)
```c
int runTrampolineClient(SOCKET *outSocket, int argc, char **argv, char **envp) {
  char *desktopPortString;

  desktopPortString = getenv("DESKTOP_PORT");

  if (desktopPortString == NULL) {
    fprintf(stderr, "ERROR: Missing DESKTOP_PORT environment variable\n");
    return 1;
  }

  unsigned short desktopPort = atoi(desktopPortString);

  SOCKET socket = openSocket();

  if (socket == INVALID_SOCKET) {
    printSocketError("ERROR: Couldn't create TCP socket");
    return 1;
  }

  *outSocket = socket;

  if (connectSocket(socket, desktopPort) != 0) {
    printSocketError("ERROR: Couldn't connect to 127.0.0.1:%d - Please make "
                     "sure you don't have an antivirus or firewall blocking "
                     "this connection.", desktopPort);
    return 1;
  }

  // Send the number of arguments (except the program name)
  char argcString[MAXIMUM_NUMBER_LENGTH];
  snprintf(argcString, MAXIMUM_NUMBER_LENGTH, "%d", argc - 1);
  WRITE_STRING_OR_EXIT("number of arguments", argcString);

  // Send each argument separated by \0
  for (int idx = 1; idx < argc; idx++) {
    WRITE_STRING_OR_EXIT("argument", argv[idx]);
  }

  // Get the number of environment variables
  char *validEnvVars[NUMBER_OF_VALID_ENV_VARS + 1];
  validEnvVars[0] = "DESKTOP_TRAMPOLINE_IDENTIFIER=" DESKTOP_TRAMPOLINE_IDENTIFIER;
  int envc = 1;
  for (char **env = envp; *env != 0; env++) {
    if (isValidEnvVar(*env)) {
      validEnvVars[envc] = *env;
      envc++;
    }
  }

  // Send the number of environment variables
  char envcString[MAXIMUM_NUMBER_LENGTH];
  snprintf(envcString, MAXIMUM_NUMBER_LENGTH, "%d", envc);
  WRITE_STRING_OR_EXIT("number of environment variables", envcString);

  // Send the environment variables
  for (int idx = 0; idx < envc; idx++) {
    WRITE_STRING_OR_EXIT("environment variable", validEnvVars[idx]);
  }

  char stdinBuffer[BUFFER_LENGTH + 1];
  int stdinBytes = 0;

  #ifdef CREDENTIAL_HELPER
    stdinBytes = fread(stdinBuffer, sizeof(char), BUFFER_LENGTH, stdin);
  #endif

  stdinBuffer[stdinBytes] = '\0';
  WRITE_STRING_OR_EXIT("stdin", stdinBuffer);
```
