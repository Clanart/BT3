No `GIT_ALLOW_PROTOCOL` restriction is set anywhere in `envForRemoteOperation`/`envForProxy`, and `addRemote` performs no protocol allowlisting before calling `git remote add`.

### Title
Unvalidated `clone_url`/`ssh_url` from GitHub API PR objects passed directly to `git remote add`, enabling protocol/argument smuggling on fetch - (File: app/src/lib/git/remote.ts, app/src/lib/stores/app-store.ts)

### Summary
When a user opens a pull request from Desktop (via the `x-github-client://openRepo/...?pr=NNN` deep link or the in-app "Checkout PR" flow), Desktop takes `head.repo.clone_url` straight from the GitHub API response and feeds it unchecked into `git remote add` and later `git fetch`. There is no allowlist of protocols (`https:`/`ssh:`/`git@`) anywhere on this path, unlike the parsing done in `app/src/lib/remote-parsing.ts` which is used only for display/name derivation, not validation.

### Finding Description
`_findPullRequestBranch` in `app/src/lib/stores/app-store.ts` receives `headCloneUrl` from the caller and, if no existing remote matches it, calls: [1](#0-0) 

This value originates directly from the GitHub API pull-request payload with no sanitization: [2](#0-1) [3](#0-2) 

`addRemote` passes the URL verbatim as a `git remote add <name> <url>` argument with no protocol check: [4](#0-3) 

The subsequent fetch (`this._fetchRemote(...)`) is executed with an environment built by `envForRemoteOperation`/`envForProxy`, which only special-cases `http(s)` for proxy resolution and never sets `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` to restrict which git transport helpers may run: [5](#0-4) 

Because git supports remote-helper transports invoked by URL scheme (e.g. `ext::`, `fd::`), and Desktop does not constrain `GIT_ALLOW_PROTOCOL` or `protocol.<name>.allow`, any value returned as `clone_url`/`ssh_url` in a PR API response is trusted as-is. This is squarely the "attacker controls...a GitHub API object...or a git remote/proxy response" scenario named in scope: a malicious or compromised GitHub Enterprise Server / MITM proxy responding to Desktop's `fetchPullRequest`/`fetchRepositoryCloneInfo` calls can substitute an attacker-chosen URL for `clone_url`. Note that `fetchRepositoryCloneInfo` also selects `ssh_url`/`clone_url` straight from the API with no validation: [6](#0-5) 

This mirrors the Arrakis H-4 pattern: a value ("module"/here, "remote URL") that is nominally set by a semi-trusted party is forwarded into a sensitive operation (fund transfer / `git remote add` + `fetch`) without any post-hoc validation of its safety, and the guard that exists elsewhere in the codebase (the strict `remoteRegexes` allowlist in `remote-parsing.ts`) is not actually applied on this call path.

### Impact Explanation
If exploited via the described paths (malicious GHES instance, compromised network proxy performing MITM on the GitHub API responses Desktop trusts, or a subverted enterprise API endpoint), a crafted `clone_url` could invoke a non-standard git transport (subject to the locally installed git version's default protocol allowances) reachable purely by the user opening a PR link/using "View on GitHub"/checking out a PR — no local access or pre-existing malware required. At minimum this is unauthenticated/unvalidated redirection of fetch/credential traffic to an attacker-controlled endpoint (credential exfiltration via `envForAuthentication()`/credential helper being invoked against an attacker URL), and in the worst case (older git versions or misconfigured `protocol.*.default` on the user's machine) command execution via remote-helper protocols.

### Likelihood Explanation
Requires the attacker to control (or MITM) a GitHub API response Desktop consumes for `clone_url`/`ssh_url` — this is not exploitable by an ordinary malicious PR author against github.com since GitHub itself generates `clone_url` for a repository, but it is a valid concern for GitHub Enterprise Server deployments (self-hosted, attacker-influenced instance) or a network-level proxy/MITM scenario, which is explicitly in-scope per the report's "attacker controls...a git remote/proxy response" criterion. Modern git's protocol allowlist defaults (`protocol.*.default=user`) provide some mitigation for exotic transports, but Desktop does nothing itself to enforce `https`/`ssh` only, so the guarantee currently rests entirely on the embedded git's own defaults rather than an explicit application-level control.

### Recommendation
Validate `clone_url`/`ssh_url`/any URL sourced from GitHub API responses against the same strict allowlist already implemented in `app/src/lib/remote-parsing.ts` (`parseRemote`) before calling `addRemote`, `setRemoteURL`, or using it as a fetch/clone source. Additionally, explicitly set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or the local equivalent `protocol.*.allow` config) in `envForRemoteOperation` for all remote-touching git invocations so Desktop does not depend solely on the embedded git binary's own default protocol policy.

### Proof of Concept
1. Point Desktop at a GitHub Enterprise Server instance controlled by, or reachable via a MITM position relative to, the attacker (or otherwise cause a crafted response to the `repos/{owner}/{name}/pulls/{n}` or `repos/{owner}/{name}` API call Desktop makes).
2. Return a PR JSON body where `head.repo.clone_url` is set to a URL using a non-`https`/`ssh` transport scheme (e.g. an `ext::`-style remote helper URL) instead of a normal GitHub clone URL.
3. The user opens the PR from Desktop's UI (or via `x-github-client://openRepo/...?pr=NNN`), triggering `openPullRequestFromUrl` → `_checkoutPullRequest` → `_findPullRequestBranch` → `addRemote(repository, forkRemoteName, headCloneUrl)` → `_fetchRemote`.
4. Because no protocol allowlist is applied at any point in this call chain [4](#0-3) [1](#0-0) , git executes the fetch using whatever transport the attacker specified, subject only to the local git binary's own default protocol policy — which Desktop neither audits nor overrides.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8646-8660)
```typescript

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1890-1903)
```typescript
  private getRepositoryFromPullRequest(
    pullRequest: IAPIPullRequest
  ): RepositoryWithGitHubRepository | null {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const headUrl = pullRequest.head.repo?.clone_url
    const baseUrl = pullRequest.base.repo?.clone_url

    // This likely means that the base repository has been deleted
    // and we don't support checking out from refs/pulls/NNN/head
    // yet so we'll bail for now.
    if (headUrl === undefined || baseUrl === undefined) {
      return null
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2039-2045)
```typescript
    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )
```

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/environment.ts (L76-139)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}

/**
 * Not intended to be used directly. Exported only in order to
 * allow for testing.
 *
 * @param remoteUrl The remote url to resolve a proxy for.
 * @param env       The current environment variables, defaults
 *                  to `process.env`
 * @param resolve   The method to use when resolving the proxy url,
 *                  defaults to `resolveGitProxy`
 */
export async function envForProxy(
  remoteUrl: string,
  env: NodeJS.ProcessEnv = process.env,
  resolve: (url: string) => Promise<string | undefined> = resolveGitProxy
): Promise<Record<string, string | undefined> | undefined> {
  const protocolMatch = /^(https?):\/\//i.exec(remoteUrl)

  // We can only resolve and use a proxy for the protocols where cURL
  // would be involved (i.e http and https). git:// relies on ssh.
  if (protocolMatch === null) {
    return
  }

  // Note that HTTPS here doesn't mean that the proxy is HTTPS, only
  // that all requests to HTTPS protocols should be proxied. The
  // proxy protocol is defined by the url returned by `this.resolve()`
  const proto = protocolMatch[1].toLowerCase() // http or https

  // We'll play it safe and say that if the user has configured
  // the ALL_PROXY environment variable they probably know what
  // they're doing and wouldn't want us to override it with a
  // protocol-specific proxy. cURL supports both lower and upper
  // case, see:
  // https://github.com/curl/curl/blob/14916a82e/lib/url.c#L2180-L2185
  if ('ALL_PROXY' in env || 'all_proxy' in env) {
    log.info(`proxy url not resolved, ALL_PROXY already set`)
    return
  }

  // Lower case environment variables due to
  // https://ec.haxx.se/usingcurl/usingcurl-proxies#http_proxy-in-lower-case-only
  const envKey = `${proto}_proxy` // http_proxy or https_proxy

  // If the user has already configured a proxy in the environment
  // for the protocol we're not gonna override it.
  if (envKey in env || (proto === 'https' && 'HTTPS_PROXY' in env)) {
    log.info(`proxy url not resolved, ${envKey} already set`)
    return
  }

  const proxyUrl = await resolve(remoteUrl).catch(err => {
    log.error('Failed resolving Git proxy', err)
    return undefined
  })

  return proxyUrl === undefined ? undefined : { [envKey]: proxyUrl }
}
```

**File:** app/src/lib/api.ts (L1010-1030)
```typescript
  public async fetchRepositoryCloneInfo(
    owner: string,
    name: string,
    protocol: GitProtocol | undefined
  ): Promise<IAPIRepositoryCloneInfo | null> {
    const response = await this.ghRequest('GET', `repos/${owner}/${name}`, {
      // Make sure we don't run into cache issues when fetching the repositories,
      // specially after repositories have been renamed.
      reloadCache: true,
    })

    if (response.status === HttpStatusCode.NotFound) {
      return null
    }

    const repo = await parsedResponse<IAPIRepository>(response)
    return {
      url: protocol === 'ssh' ? repo.ssh_url : repo.clone_url,
      defaultBranch: repo.default_branch,
    }
  }
```
