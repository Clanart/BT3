Based on the investigation, I found a real, code-documented analog of the "downstream operation doesn't get the right forwarded context" bug class in GitHub Desktop's submodule/proxy environment handling.

### Title
Submodule update reuses a single proxy/auth environment resolved for the primary remote, not for each submodule's actual host - (File: `app/src/lib/git/submodule.ts`)

### Summary
The Uniswap report's broken invariant is: a dispatcher resolves and forwards a value (native ETH) computed for the *top-level* call, but the actual downstream call target (v4 Position Manager) needs its own correctly-scoped value, and the forwarding logic never re-derives/re-sends it per sub-call. The Desktop analog is `updateSubmodulesAfterOperation` in [1](#0-0) , which resolves a single `envForRemoteOperation(...)` (proxy/auth environment) using only the top-level repository's remote URL, then reuses that single environment for the entire `git submodule update --init --recursive` invocation, even though recursive submodule init/update will connect to whatever remote URLs are declared in `.gitmodules`, which can point to arbitrary, attacker-controlled hosts.

### Finding Description
`envForRemoteOperation` is documented as resolving proxy configuration for "the primary remote URL for this operation," and the doc comment itself acknowledges the exact failure mode: "Git might connect to other remotes in order to fulfill the operation... a clone of `https://github.com/desktop/desktop` could contain a submodule pointing to another host entirely" [2](#0-1) .

Despite this documented caveat, `updateSubmodulesAfterOperation` computes the proxy/auth environment exactly once, from `getFallbackUrlForProxyResolve(repository, remote)`, and passes that single, statically-resolved environment object to the one `git submodule update --init --recursive` call [3](#0-2) . Git then internally clones/fetches every submodule listed in `.gitmodules` (fully attacker-controlled content in a cloned/fetched repository) using this same, single environment - there is no per-submodule-host re-resolution of proxy settings anywhere in this call path.

This mirrors the Uniswap bug precisely: a dispatch/orchestration layer forwards one value computed for the "outer" operation into an inner operation whose real target differs, and no guard exists to re-derive the value for the differing target.

### Impact Explanation
An attacker who controls a repository's `.gitmodules` (a cloned/fetched, attacker-influenced object) can point a submodule at an arbitrary host. Because the proxy environment is resolved only once for the primary remote, submodule traffic to the attacker's host either:
- Bypasses a proxy the user's environment/organization relies on for network egress control (e.g., corporate proxy required for all outbound git traffic), or
- Is routed through a proxy resolved for a different host, potentially exposing proxy authentication material (`http(s)_proxy` credentials embedded in the resolved URL) to a network path not intended for that host.

This is a network-path/policy-bypass issue rather than a demonstrated direct code-execution or credential-exfiltration primitive; I could not find code in this path that discloses GitHub-scoped credentials to the wrong host (per-host credential lookup happens later via the trampoline credential helper, which does check the requested URL/endpoint, per `getCredential` in `trampoline-credential-helper.ts`). The severity is therefore more moderate than the "code execution / credential exfiltration" bar in the Valid Impact list, and I want to be explicit about that limitation rather than overstate it.

### Likelihood Explanation
Triggering this only requires the victim to clone or fetch a repository containing a `.gitmodules` file with a submodule URL pointing to a different host, and then run "Update Submodules" or any operation that calls `updateSubmodulesAfterOperation` (checkout, pull, clone with submodules, etc.) - all of which are normal, expected user actions with no unnatural steps.

### Recommendation
Resolve (or re-resolve) the proxy/auth environment per submodule URL rather than once for the top-level remote - e.g., by parsing `.gitmodules` prior to invoking `git submodule update` and computing `envForRemoteOperation` per distinct submodule host, or by using `GIT_CONFIG_PARAMETERS`/`insteadOf`-based per-host proxy config so Git itself resolves proxy settings per connection rather than Desktop pre-computing one value for the whole recursive operation.

### Proof of Concept
1. Attacker publishes a public repo whose `.gitmodules` contains a submodule URL pointing to `https://attacker-controlled-host.example/`.
2. Victim, using GitHub Desktop with a proxy configured (e.g., required for network policy reasons) that resolves correctly for `github.com`, clones the repo and runs a submodule update.
3. `updateSubmodulesAfterOperation` resolves the proxy environment once via `envForRemoteOperation(getFallbackUrlForProxyResolve(repository, remote))` based on the primary GitHub remote [4](#0-3) .
4. `git submodule update --init --recursive` runs with that single environment and fetches from `attacker-controlled-host.example`, using proxy settings that were never resolved for that host - as explicitly foreseen (but not mitigated) in the `envForRemoteOperation` docstring [5](#0-4) .

**Caveat:** I was not able to fully verify inside the given time whether `envForProxy`'s per-host resolution (`resolveGitProxy`) embeds credentials in a way that would leak to the wrong host, since the body of `envForProxy` beyond its signature was not retrieved. If you need certainty on that point, a Devin session with full repo access should inspect `app/src/lib/git/environment.ts` in full, particularly `resolveGitProxy` and `envForAuthentication`.

### Citations

**File:** app/src/lib/git/submodule.ts (L29-55)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
    return
```

**File:** app/src/lib/git/environment.ts (L59-81)
```typescript
/**
 * Create a set of environment variables to use when invoking a Git
 * subcommand that needs to communicate with a remote (i.e. fetch, clone,
 * push, pull, ls-remote, etc etc).
 *
 * The environment variables deal with setting up sane defaults, configuring
 * authentication, and resolving proxy urls if necessary.
 *
 * @param account   The authentication information (if available) to provide
 *                  to Git for use when connecting to the remote
 * @param remoteUrl The primary remote URL for this operation. Note that Git
 *                  might connect to other remotes in order to fulfill the
 *                  operation. As an example, a clone of
 *                  https://github.com/desktop/desktop could contain a submodule
 *                  pointing to another host entirely. Used to resolve which
 *                  proxy (if any) should be used for the operation.
 */
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```
