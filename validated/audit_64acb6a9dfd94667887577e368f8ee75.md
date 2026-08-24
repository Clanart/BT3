## Title
Malicious repository Git hooks inherit Desktop's `credential.helper=desktop` trampoline environment, allowing exfiltration of the user's GitHub OAuth token - (File: `app/src/lib/trampoline/trampoline-environment.ts`)

### Summary
This is a real Desktop analog to the external report's "nested protected function" class of bug. In the smart-contract report, a policy guard (e.g. `hasEnteredConsumer` / per-call approvals) is not properly scoped to nested/child invocations, so an inner call inherits or bypasses the outer call's trust context. In GitHub Desktop, the trampoline mechanism that lets Git call back into the app for credentials sets `credential.helper=desktop` **globally for the whole git process tree** via `GIT_CONFIG_PARAMETERS`, and hands out `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` in the process environment [1](#0-0) . Any child process spawned by that Git invocation — including a repository-controlled hook script (`post-checkout`, `post-merge`, `pre-push`, etc.) — inherits that environment and can therefore speak the credential-helper protocol directly to Desktop's trampoline server and request credentials for an arbitrary host, not just the one actually being contacted.

### Finding Description
`withTrampolineEnv` configures every Git subcommand with:
- `DESKTOP_PORT` / `DESKTOP_TRAMPOLINE_TOKEN` (needed to reach the trampoline TCP server) [2](#0-1) 
- `GIT_CONFIG_PARAMETERS` forcing `credential.helper=desktop` so that "commands invoked by filters (i.e. Git LFS)... pick up our configuration" — explicitly because arguments are not passed to child processes/filters, so env vars are used instead [3](#0-2) 

This same reasoning applies to Git hooks: hooks are spawned as children of the `git` process performing the operation, and they inherit the process environment, including `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `GIT_CONFIG_PARAMETERS`. A hook script (or an LFS smudge/clean filter, or a submodule handler) that is part of an attacker-controlled cloned/fetched repository can therefore run `git credential-desktop get` (or speak the trampoline TCP protocol directly on `DESKTOP_PORT` with the leaked `DESKTOP_TRAMPOLINE_TOKEN`) and supply arbitrary `protocol=https`/`host=github.com` values on stdin.

On the Desktop side, `createCredentialHelperTrampolineHandler` validates the token only for authenticity (`isValidTrampolineToken`, `trampoline-server.ts`) — it does **not** verify that the requested host/endpoint matches the repository's actual remote for that operation. The handler resolves credentials purely from the `cred` map supplied by the caller: [4](#0-3) 

`getGitHubCredential` looks up the account by `endpoint` and returns the account's real OAuth `token`: [5](#0-4) 

Because the token is valid for the entire lifetime of the outer operation (not scoped per-child-process, per-hook, or per-remote), and the credential lookup trusts whatever `host`/`protocol` the caller claims, a nested/child process — analogous to the "nested protected function" in the smart-contract report calling back into the same trust boundary — can obtain credentials the guard was never intended to release to it.

The existing guard (`isValidTrampolineToken`) only proves the request came from *some* process spawned under a legitimate Desktop-initiated Git operation; it does not prove the request corresponds to the *actual remote URL* being contacted for that operation, which is the invariant an attacker can violate here.

### Impact Explanation
A malicious repository shipped with an executable hook (e.g. `post-checkout`, `post-merge`, `pre-push`) or a Git LFS/clean-filter script is executed automatically by Git during ordinary Desktop operations (clone, fetch, checkout, pull, commit). By requesting `protocol=https\nhost=github.com` from the inherited credential-helper environment, the hook receives the signed-in user's GitHub OAuth token as stored in Desktop, without any UI prompt (assuming an account for that endpoint already exists — the common case for most Desktop users). This is credential/token exfiltration driven entirely by content the attacker controls in the cloned repository, matching the "attacker controls a cloned/fetched repository ... resulting in ... credential/token exfiltration" criteria.

### Likelihood Explanation
Git hooks are enabled by default for any repository unless explicitly disabled via `interceptHooks`/`getHooksEnvEnabled` gating in `withHooksEnv` [6](#0-5) , and the set of hook names Desktop is even aware of (`knownHooks`) is broad, covering routine operations like `post-checkout`/`post-merge`/`pre-push` [7](#0-6) . Because these hooks run as normal OS child processes of the `git` invocation (not sandboxed to the trampoline's intended credential scope), and the credential trampoline validates only token authenticity — not the requesting host against the operation's actual remote — this requires no unusual user interaction beyond a normal clone/fetch/checkout of a hostile repository.

Note: I could not fully verify from the indexed code whether `with-hooks-env.ts`'s own hook-proxy mechanism (which redirects hooks through `PROCESS_PROXY_PORT`/`PROCESS_PROXY_TOKEN` and a separate `core.hooksPath`) strips or otherwise sanitizes `GIT_CONFIG_PARAMETERS`/`DESKTOP_TRAMPOLINE_TOKEN` from the hook's spawned environment before executing the real hook body — the proxy plumbing (`hooks-proxy.ts`, `process-proxy`) was not fully inspected due to iteration limits, so it's possible (but not confirmed) that this proxy layer, if it forwards the outer `opts.env` verbatim to hook execution, preserves the trampoline variables. This should be verified directly by an engineer before treating this as fully confirmed.

### Recommendation
- Scope trampoline credential requests to the specific remote/endpoint associated with the operation that created the token, and reject `get` requests whose `host`/`protocol` do not match an allow-list derived from the actual remote(s) being operated on (including known LFS/submodule remotes) rather than trusting the caller-supplied `host` value outright.
- When executing repository-controlled hooks (via `withHooksEnv`/`hooks-proxy.ts`), strip `DESKTOP_TRAMPOLINE_TOKEN`, `DESKTOP_PORT`, and the `credential.helper=desktop` `GIT_CONFIG_PARAMETERS` entry from the environment passed to the hook's proxy process unless the hook is expected to need Git network credentials for the same operation.
- Document (as the referenced report recommends for combined/nested policies) which Desktop-internal trust boundaries are and are not safe to expose to subprocess/hook execution contexts.

### Proof of Concept
Not independently executed against a live build; this is derived from static code review of the trampoline plumbing. Conceptually:
1. Clone or fetch an attacker-controlled repository containing an executable `post-checkout` hook.
2. During Desktop's clone/checkout, Git spawns the hook as a child process, inheriting `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `GIT_CONFIG_PARAMETERS` set by `withTrampolineEnv` [1](#0-0) .
3. The hook script runs `git credential-desktop get` (or connects to `127.0.0.1:$DESKTOP_PORT` directly per the trampoline protocol) with stdin `protocol=https\nhost=github.com\n`.
4. `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential` returns the signed-in user's OAuth token for `github.com` [8](#0-7) , which the hook can exfiltrate over the network.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L123-147)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-57)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)

async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-99)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }
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

**File:** app/src/lib/hooks/get-repo-hooks.ts (L10-39)
```typescript
const knownHooks = [
  'applypatch-msg',
  'pre-applypatch',
  'post-applypatch',
  'pre-commit',
  'pre-merge-commit',
  'prepare-commit-msg',
  'commit-msg',
  'post-commit',
  'pre-rebase',
  'post-checkout',
  'post-merge',
  'pre-push',
  'pre-receive',
  'update',
  'proc-receive',
  'post-receive',
  'post-update',
  'reference-transaction',
  'push-to-checkout',
  'pre-auto-gc',
  'post-rewrite',
  'sendemail-validate',
  'fsmonitor-watchman',
  'p4-changelist',
  'p4-prepare-changelist',
  'p4-post-changelist',
  'p4-pre-submit',
  'post-index-change',
]
```
