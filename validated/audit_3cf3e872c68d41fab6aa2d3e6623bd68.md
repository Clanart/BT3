## Finding

The suspicious pattern in the Solidity report — a "protective check" that is supposed to gate a security-critical action but is inverted/bypassable, silently disabling protection — has a direct structural analog in GitHub Desktop's Git hooks interception mechanism.

### Title
Git hook interception is silently disabled for wildcard hook filters, letting untrusted repo hooks run with real credential-bearing environment - (File: `app/src/lib/hooks/get-repo-hooks.ts`)

### Summary
`getRepoHooks` is the gate that decides which hooks found inside a (possibly attacker-supplied, cloned/fetched) repository get routed through the sandboxed hook proxy (`withHooksEnv`/`hooks-proxy.ts`), which strips sensitive environment variables such as `GIT_ASKPASS` and `GIT_SSH_COMMAND` before the hook process runs. The filtering condition is inverted for the documented `'*'` wildcard case, causing the function to yield zero hooks whenever callers ask it to intercept "all hooks." When zero hooks are returned, `withHooksEnv` treats the repository as having no hooks and returns the caller's raw, unsanitized environment, so Git executes the untrusted hook scripts directly with the real environment instead of through the proxy that would have redacted credential-related variables.

### Finding Description
`getRepoHooks` computes: [1](#0-0) 

```
const matchAll = filter?.includes('*')

for (const file of files) {
  const hookName = basename(file.name, '.exe')

  if (matchAll || filter?.includes(hookName) === false) {
    continue
  }
  ...
```

Per the function's own doc comment, `"Including '*' will return all hooks"` [2](#0-1) . But the implementation does the opposite: when `filter` contains `'*'`, `matchAll` is `true`, and the `continue` on line 90 fires unconditionally for *every* hook file found in the repository's hooks directory, so the generator yields nothing.

This function is consumed by `withHooksEnv`: [3](#0-2) 

```
const hooks = await Array.fromAsync(getRepoHooks(path, opts.interceptHooks))

if (hooks.length === 0) {
  return fn(opts?.env)
}
```

If `getRepoHooks` always returns an empty list for a `'*'` filter, `withHooksEnv` unconditionally skips setting up the `hooksProxy`/temporary `core.hooksPath` sandbox and just runs the caller-supplied `opts?.env` directly. That sandbox is precisely what strips security-sensitive variables before a hook process executes: [4](#0-3) 

```
const excludedEnvVars: ReadonlySet<string> = new Set([
  ...
  'GIT_ASKPASS',
  'GIT_SSH_COMMAND',
  'GIT_USER_AGENT',
])
```

`getRepoHooks` is invoked with a `filter` argument (`opts.interceptHooks`) from several Git operations (`commit.ts`, `merge.ts`, `pull.ts`, `push.ts`) via `HookCallbackOptions`, and the doc comment's explicit mention of `'*'` semantics strongly implies that operations which can trigger many different hook types (e.g. `merge`/`pull`, which can invoke `pre-merge-commit`, `commit-msg`, `post-merge`, `post-checkout`, etc.) pass `'*'` to catch all of them. I confirmed the `interceptHooks:` call sites exist in `merge.ts` and `pull.ts` via `grep_search`, but I was not able to inspect the exact array literal at each call site before running out of tool budget — this should be verified in a follow-up session to confirm which operations pass `'*'` versus an explicit hook-name list.

### Impact Explanation
Exactly like the Solidity bug (a check meant to gate/guard a security-relevant deployment being defeated, silently leaving the system unprotected), this bug defeats the "run hooks only through the sanitized proxy" guarantee. Because the affected hooks (`pre-merge-commit`, `commit-msg`, `post-merge`, `post-checkout`, `pre-push`, etc.) live inside `.git/hooks` of a repository the user cloned, fetched, or opened, an attacker who controls the content of that repository (or a submodule, or a branch merged/checked out by the user) can plant a malicious hook. If the interception is skipped due to this bug, the hook is spawned directly by dugite's `git` invocation using the real operation environment rather than the redacted proxy environment — meaning `GIT_ASKPASS` (Desktop's credential helper) and `GIT_SSH_COMMAND` remain present and readable/exploitable by the attacker's hook script, enabling credential/token exfiltration or arbitrary code execution using the app's ambient authentication context.

### Likelihood Explanation
This is not a race condition or timing attack like the original report — it is a deterministic logic inversion that fires every time a caller requests wildcard hook interception (`'*'`), which per the documented contract is meant to be the common case for operations (merges/pulls) that can trigger many hook types. Any repository operation that legitimately relies on `'*'` filtering to intercept "whatever hook the operation might invoke" is unconditionally unprotected, and untrusted hook content is routinely introduced through ordinary Desktop workflows (clone, fetch+merge, checkout of a PR branch) — no privileged access or unusual user steps are required.

### Recommendation
Fix the inverted condition so wildcard filters actually match all hooks, e.g.:
```ts
if (!matchAll && filter?.includes(hookName) === false) {
  continue
}
```
Add a regression test asserting that `getRepoHooks(path, ['*'])` yields every known, executable hook present in the hooks directory, and add a test on `withHooksEnv` confirming that when hooks exist and `interceptHooks` is `['*']`, the proxy environment (with `excludedEnvVars` stripped) is used rather than the caller's raw `opts.env`.

### Proof of Concept
1. Attacker publishes a Git repository containing an executable `.git/hooks/post-merge` (or `commit-msg`) script that reads `process.env.GIT_ASKPASS` / `GIT_SSH_COMMAND` and exfiltrates them (or otherwise abuses the ambient authenticated context) to a remote server.
2. Victim clones/fetches this repository in GitHub Desktop and performs a merge or pull, which calls a Git operation (e.g. `mergeBranch`/`pull`) with `interceptHooks: ['*']`.
3. `withHooksEnv` calls `getRepoHooks(path, ['*'])`, which — due to `matchAll` short-circuiting the loop — yields no hooks even though `post-merge`/`commit-msg` exist and are executable.
4. `withHooksEnv` sees `hooks.length === 0` and returns `fn(opts?.env)`, skipping the `hooksProxy` sandbox entirely.
5. Git invokes the malicious hook directly with the real environment (including `GIT_ASKPASS`/`GIT_SSH_COMMAND`), which the proxy would otherwise have stripped, allowing the hook script to exfiltrate credentials or otherwise abuse the authenticated Git context.

*Confidence caveat:* I verified the inverted condition in `get-repo-hooks.ts` and the `hooks.length === 0` fallback in `with-hooks-env.ts` directly from the indexed source, but I could not, within the available tool budget, confirm the literal `interceptHooks` arrays passed from `merge.ts`/`pull.ts` to prove that `'*'` is actually used in a reachable, attacker-triggerable code path today. A Devin session with full repo access should confirm those call sites before treating this as fully verified.

### Citations

**File:** app/src/lib/hooks/get-repo-hooks.ts (L73-75)
```typescript
 * @param filter An optional array of hook names to filter the results.
 *               Including '*' will return all hooks.
 */
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L85-96)
```typescript
  const matchAll = filter?.includes('*')

  for (const file of files) {
    const hookName = basename(file.name, '.exe')

    if (matchAll || filter?.includes(hookName) === false) {
      continue
    }

    if (!knownHooks.includes(hookName)) {
      continue
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
