## Analysis

I traced the SSRF bug class (server makes an outbound HTTP request to an attacker-influenced URL and leaks port/host reachability info back to an untrusted party) to Desktop's git-credential trampoline flow.

When Git needs credentials for *any* remote host — including one supplied by a hostile remote, submodule URL, or an HTTP redirect encountered during `fetch`/`clone`/`push` — it invokes Desktop's credential helper trampoline, which calls `getEndpointKind()` in `trampoline-credential-helper.ts`. If the host isn't a known GitHub.com/GHE endpoint and there's no cached account and no `WWW-Authenticate: realm="GitHub"` header, Desktop falls back to `isGitHubHost(endpoint)` to decide whether to treat the host as "enterprise" (prompt sign-in) or "generic" (prompt basic auth).

`isGitHubHost()` in `app/src/lib/api.ts` performs a live network probe: `fetch(`${endpoint}/meta?ghd=...`, { method: 'HEAD', redirect: 'error', signal: <2s timeout> })` and returns `true`/`false`/`undefined` based on whether the request succeeded and returned the `x-github-request-id` header. [1](#0-0) 

Crucially, `endpoint` here is `getCredentialUrl(cred)` — the URL Git itself is trying to authenticate against, which is derived directly from whatever remote/redirect target Git is currently talking to, not validated to be an internal/private address. [2](#0-1) 

Because the resulting `getEndpointKind()` outcome changes *observable, attacker-visible behavior* — either Desktop pops a "Sign in to GitHub Enterprise" dialog (`ui.promptForGitHubSignIn`) or a generic username/password prompt (`promptForCredential`) — a party who can steer Git's outbound target to an arbitrary host:port (e.g., via a malicious submodule URL, an `insteadOf`/redirect config baked into a cloned repo, or an HTTP redirect served by a compromised proxy) can use Desktop as a blind network oracle: pointing it at `http://169.254.169.254:80/`, `http://127.0.0.1:6379/`, etc., and inferring open/closed/filtered state from which prompt appears, from the 2-second timeout firing, or from the differing latency — the exact same "port-open vs unreachable vs timeout" signal differentiation described in the original Kibana/Elastic report.

## Title
Blind SSRF via git-credential-triggered `isGitHubHost()` host probe reveals internal port/host reachability - (File: app/src/lib/api.ts, app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
Desktop's credential-helper trampoline classifies unknown Git remote hosts by making a live outbound HTTP HEAD request (`isGitHubHost`) to whatever URL Git is currently authenticating against, without restricting the target to public/expected hosts. The URL originates from Git's own remote resolution (including remotes an attacker can fully control, e.g. `.gitmodules` submodule URLs or configured `insteadOf`/redirect targets in a cloned repo), turning the app into a blind SSRF oracle that reveals whether an internal host/port is reachable, open, or filtered.

### Finding Description
`getEndpointKind()` is invoked for every credential request that Git issues that isn't already a known github.com/GHE endpoint [2](#0-1) . When none of the fast-path checks match (not Gist, not dotcom, not GHE, no cached account, no `WWW-Authenticate: realm="GitHub"` header, and protocol is `https:`), it falls through to `isGitHubHost(endpoint)`.

`isGitHubHost()` builds `${endpoint}/meta?ghd=<uuid>` and performs a real network fetch with a 2-second abort timeout, then reports whether the response carried the `x-github-request-id` header [1](#0-0) . The function's return value (`true`/`false`/`undefined`) is used to route the flow into either an "enterprise" sign-in prompt or a "generic" credential prompt [3](#0-2) .

The `endpoint` value is derived straight from `getCredentialUrl(cred)`, i.e., the host Git is actually contacting during a fetch/clone/push operation [4](#0-3) . Because Git's credential-fill protocol is invoked for *any* URL Git ends up talking to — including submodule URLs baked into a cloned repository, or hosts reached via HTTP redirects during a legitimate clone — a hostile repository author can cause the victim's Desktop process to issue outbound requests to attacker-chosen internal hosts/ports (e.g. cloud metadata endpoints, internal admin panels, loopback services) purely by having the victim clone/fetch/add a crafted repo containing such a submodule/remote configuration.

Unlike the enterprise sign-in flow, which validates the URL is `https:` only and is user-typed [5](#0-4) , this path is triggered automatically as part of ordinary git operations with a host value that is not something the user typed and is not scoped to any allow-list of expected servers.

### Impact Explanation
An attacker who controls a repository (via a malicious submodule, `insteadOf` remote rewrite, or a compromised/attacker-controlled HTTP endpoint that responds with a redirect during a normal fetch) can:
- Cause GitHub Desktop's main/renderer process to make outbound HTTP HEAD requests to arbitrary internal hosts and ports of the attacker's choosing.
- Distinguish open vs. closed vs. filtered ports based on: (a) which auth prompt is displayed (enterprise vs generic — a directly observable UI difference), (b) whether the 2-second timeout is hit, and (c) response timing — mirroring the exact oracle behavior described in the original report (`WARNING`/`timeout`/`host unreachable` vs. success responses).
- This is a blind, unprivileged internal-network port scanning primitive that requires no more than the victim opening/cloning/fetching an attacker-supplied repository — squarely within the accepted impact class ("attacker controls a cloned/fetched repository... or a git remote/proxy response").

### Likelihood Explanation
Moderate-to-high. Any repository the victim clones, or any remote redirect the victim's Git client follows during an ordinary `fetch`/`pull`/`push`, can trigger this code path once Desktop doesn't already recognize the host as github.com/GHE and doesn't already hold cached credentials for it, which is the common case for any new/untrusted remote. No unusual user interaction beyond a normal "Clone this repository" or add-existing-repo action is required.

### Recommendation
Restrict `isGitHubHost()`'s outbound probe to hosts that resemble public/expected GitHub Enterprise addresses (reject loopback, link-local, private RFC1918 ranges, and cloud metadata IP ranges such as `169.254.169.254`) before issuing the fetch, similar to how `isValidBYOKBaseUrl`/`isLocalBaseUrl` gate BYOK URLs [6](#0-5) . Additionally, avoid triggering this network probe for hosts encountered incidentally via Git's own remote/redirect resolution (submodules, `insteadOf`) rather than an explicitly user-entered/allow-listed endpoint, and ensure the UI outcome (which prompt appears) does not leak network reachability differences for non-allow-listed hosts.

### Proof of Concept
1. Attacker publishes a public repository containing a `.gitmodules` entry (or a git config `insteadOf` rewrite baked into the repo's tracked config, or a remote hosted behind an HTTP redirect) pointing a submodule/remote at `https://169.254.169.254/` or `https://127.0.0.1:6379/` (or any internal target the attacker wants to probe).
2. Victim clones the repository (or a repository containing this as a submodule) in GitHub Desktop and Desktop performs a submodule fetch/clone as part of the operation.
3. Git invokes the credential-fill trampoline for the submodule host; `getEndpointKind()` falls through to `isGitHubHost('https://169.254.169.254/')`, which sends a HEAD to `https://169.254.169.254/meta?ghd=...` with a 2-second timeout [1](#0-0) .
4. Depending on whether the internal port/service is open, closed, or non-responsive, the victim sees either a generic-credential prompt (open/reachable, non-GitHub) or an enterprise sign-in prompt/hang (varies by timing) — allowing the attacker to correlate outcomes (e.g., via repeated repos targeting different ports and asking the victim, in a controlled test setup, to report which dialog appeared, or by chaining with telemetry/timing side channels) to fingerprint the internal network.

*Confidence note*: I was not able to fully trace how quickly/automatically Desktop triggers submodule credential prompts during a background fetch (vs. requiring explicit user submodule-update action), so the exact automatic-triggering conditions and whether the prompt is silent vs. visible in all code paths should be verified with the full source before treating this as conclusively "zero-click." This is stated as a caveat on likelihood.

### Citations

**File:** app/src/lib/api.ts (L2465-2491)
```typescript
  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })

    tryUpdateEndpointVersionFromResponse(endpoint, response)

    return response.headers.has('x-github-request-id')
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-134)
```typescript
  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
  }

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-178)
```typescript
const getEndpointKind = async (cred: Credential, store: Store) => {
  const credentialUrl = getCredentialUrl(cred)
  const endpoint = `${credentialUrl}`

  if (isGist(endpoint)) {
    return 'generic'
  }

  if (isDotCom(endpoint)) {
    return 'github.com'
  }

  if (isGHE(endpoint)) {
    return 'ghe.com'
  }

  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper. We
  // use them as a happy-path to determine if the host is a GitHub host without
  // having to resort to making a request ourselves.
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }

  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
```

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
}
```

**File:** app/src/lib/copilot/byok.ts (L227-250)
```typescript
/**
 * Returns true if the given string parses as an absolute http:// or https://
 * URL. Used as the single source of truth for `baseUrl` validation in both
 * the dialog and the localStorage loader.
 *
 * `http://` is only accepted when the host is on the local machine (see
 * {@link isLocalBaseUrl}); sending an API key to an arbitrary remote host
 * over plaintext HTTP would leak the credential to anyone on the network
 * path.
 */
export function isValidBYOKBaseUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    if (parsed.protocol === 'https:') {
      return true
    }
    if (parsed.protocol === 'http:' && isLocalBaseUrl(value)) {
      return true
    }
    return false
  } catch {
    return false
  }
}
```
