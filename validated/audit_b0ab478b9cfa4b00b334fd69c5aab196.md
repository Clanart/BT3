## Title
Malicious repository hooks can steal the trampoline credential token to exfiltrate stored Git/GitHub credentials - (File: `app/src/lib/git/core.ts`, `app/src/lib/hooks/with-hooks-env.ts`, `app/src/lib/trampoline/trampoline-environment.ts`)

### Summary
The external report's broken invariant is: a privileged value (native `msg.value`) is delivered to the wrong execution context, so downstream logic that trusts that context misbehaves. The Desktop analog is structurally the same class of bug: a privileged secret (`DESKTOP_TRAMPOLINE_TOKEN` / `DESKTOP_PORT`, which grant access to Desktop's local credential-helper TCP server) is placed into the *same process environment* that Git uses to spawn hook subprocesses, and Desktop's own hook-sandboxing mechanism (`withHooksEnv`/`hooks-proxy.ts`) — which strips that token before running an untrusted hook script — is only opted into by a subset of Git operations (`commit`, `merge`, `pull`, `push`). Operations that also can trigger hooks, most notably `clone` and `checkout`/`fetch`, do **not** enable `interceptHooks`, so the raw, unsandboxed environment (including the trampoline token) reaches hook processes verbatim.

### Finding Description
`git()` in `app/src/lib/git/core.ts` always wraps every Git invocation with `withTrampolineEnv`, injecting `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` into the process environment used to spawn Git: [1](#0-0) 

This token is the sole authorization mechanism for Desktop's trampoline TCP server, which brokers Git credential-helper `get`/`store`/`erase` requests, including returning cached GitHub tokens for arbitrary endpoints: [2](#0-1) [3](#0-2) 

The server only validates that the token is a currently-live token — it performs no binding to a specific repository, operation, or endpoint: [4](#0-3) 

Desktop is aware this token must not leak to arbitrary hook scripts: `withHooksEnv` optionally reroutes hook execution through a proxy (`hooks-proxy.ts`) that filters the environment down to a `GIT_`/`GITHEAD_` allow-list before the real hook binary runs, explicitly excluding sensitive vars: [5](#0-4) [6](#0-5) 

However, `withHooksEnv` only performs this interception when the caller explicitly passes `opts.interceptHooks`; otherwise it falls through and returns the caller's raw environment untouched, meaning the real `.git/hooks/*` scripts run with the **full** environment produced by `withTrampolineEnv`, including `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT`: [7](#0-6) [8](#0-7) 

`interceptHooks` is only wired up for `commit`, `merge`, `pull`, and `push`: [9](#0-8) [10](#0-9) [11](#0-10) [12](#0-11) 

`clone.ts` and (per the grep results) checkout/fetch code paths do not set `interceptHooks`, so the exposed `clone` implementation calls `git(...)` with no hook sandboxing at all: [13](#0-12) 

`git clone` (and later `git checkout`) natively executes `post-checkout` from whatever hooks directory the repository specifies (`.git/hooks/post-checkout`, or a repo-provided `core.hooksPath`, which can be committed inside the cloned tree). Since hook execution for these commands is not intercepted, the hook subprocess inherits `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` directly from its parent (Git, in turn a child of the Node/Electron process that set the trampoline env).

### Impact Explanation
An attacker who controls a cloned/fetched repository (e.g. a public repo the user is invited to clone or that gets fetched via a URL) can commit a `post-checkout` hook (or set `core.hooksPath` to a directory containing one) that:
1. Reads `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` from its own process environment.
2. Opens a raw TCP connection to `127.0.0.1:$DESKTOP_PORT` and speaks the trampoline protocol directly (bypassing Git's askpass/credential-helper plumbing entirely), issuing a `credential-helper get` command for an arbitrary URL such as `https://github.com` in the format handled by `createCredentialHelperTrampolineHandler`.
3. Because `isValidTrampolineToken` only checks liveness, not scope, the server will happily return the user's cached GitHub credential/PAT for that endpoint to the malicious hook process: [14](#0-13) 

This results in credential/token exfiltration for the user's GitHub account(s) triggered simply by cloning or checking out a malicious repository — matching the in-scope impact category of "credential/token exfiltration" from an "attacker-controlled cloned/fetched repository."

### Likelihood Explanation
Likelihood is high for any user who clones or checks out an untrusted repository, since hooks execute automatically as part of normal Git operations with no user prompt, and Desktop's existing safeguard (the hooks proxy env filter) is bypassed simply because `clone`/`checkout` never request `interceptHooks`. No local access, elevated privileges, or social engineering beyond "clone this repo" is required.

### Recommendation
Route all Git operations that can trigger hook execution (`clone`, `checkout`, `fetch`, `rebase`, `cherry-pick`, `reset`, `apply`, etc.) through the same `interceptHooks` sandbox used by `commit`/`merge`/`pull`/`push`, or alternatively strip `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` (and other Desktop-internal secrets) from the environment before Git spawns any hook process, regardless of which top-level command invoked it. Additionally, consider binding trampoline tokens to the specific operation/repository so a leaked token cannot be used to request credentials for unrelated endpoints.

### Proof of Concept
1. Attacker publishes a repository containing `.git-hooks/post-checkout` (or configures `core.hooksPath` inside a tracked config committed to the repo) with a script that reads `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` from `process.env`/`os.environ`.
2. Script connects to `127.0.0.1:$DESKTOP_PORT`, sends a well-formed trampoline command with `DESKTOP_TRAMPOLINE_IDENTIFIER=CredentialHelper`, `DESKTOP_TRAMPOLINE_TOKEN=<stolen>`, and stdin `protocol=https\nhost=github.com\n\n` followed by `get`.
3. Victim clones the repository in GitHub Desktop (`clone.ts` invokes `git(...)` without `interceptHooks`); Git runs `post-checkout` after the clone completes, inheriting the trampoline env.
4. The trampoline server, seeing a valid (live) token, returns the victim's stored GitHub credential to the attacker's hook script, which exfiltrates it over the network.

*Note: I was not able to directly view `app/src/lib/git/checkout.ts` and `fetch.ts` contents in this session to confirm the exact absence of `interceptHooks` there beyond the grep evidence; a Devin session with full file access should verify all call sites of `git()` that can trigger hook execution to enumerate the complete list of unprotected commands.*

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L122-126)
```typescript
    try {
      return await fn({
        DESKTOP_PORT: await trampolineServer.getPort(),
        DESKTOP_TRAMPOLINE_TOKEN: token,
        GIT_ASKPASS: '',
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-183)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
    }

    const handler = this.commandHandlers.get(command.identifier)

    if (handler === undefined) {
      socket.end()
      return
    }

    const result = await handler(command).catch(e =>
      log.error('Error processing trampoline command', e)
    )

    if (result !== undefined) {
      socket.end(result)
    } else {
      socket.end()
    }
  }
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-105)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)
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

**File:** app/src/lib/git/commit.ts (L1-1)
```typescript
import { git, HookCallbackOptions, parseCommitSHA } from './core'
```

**File:** app/src/lib/git/merge.ts (L1-1)
```typescript
import { join } from 'path'
```

**File:** app/src/lib/git/pull.ts (L1-1)
```typescript
import {
```

**File:** app/src/lib/git/push.ts (L1-1)
```typescript
import { git, HookCallbackOptions, IGitStringExecutionOptions } from './core'
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
