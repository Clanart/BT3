### Title
Trust decision for "is this a GitHub host" is based on an attacker-forgeable `WWW-Authenticate` realm string, allowing a malicious git server to impersonate GitHub Enterprise and trigger sign-in against attacker infrastructure - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The C4 finding shows that `Blocklist._isContract()` infers a security-relevant property (an address is a "contract" that should be blocked) from a cheap, attacker-controlled signal (`extcodesize`) instead of an unforgeable identity, letting the attacker toggle the property at will via `SELFDESTRUCT` + `CREATE2`. GitHub Desktop has the same root cause in its git credential trampoline: `getEndpointKind()` decides whether a remote host is a genuine GitHub/GitHub Enterprise server by looking for the substring `realm="GitHub"` in the `WWW-Authenticate` header that the remote server returned — a value fully controlled by whatever server the user's git operation talks to.

### Finding Description
When git performs an HTTP(S) operation against a remote and receives a `401`, it captures the `WWW-Authenticate` response header(s) and forwards them to Desktop's credential helper trampoline as `wwwauth[...]` credential fields [1](#0-0) . `getEndpointKind()` treats the mere presence of `realm="GitHub"` in that attacker-supplied string as proof the host is a GitHub Enterprise server: [2](#0-1) 

This check is explicitly a shortcut to *avoid* doing the real verification that exists elsewhere in the codebase — `isGitHubHost()` — which makes an actual HTTPS request to `${endpoint}/meta` and only trusts the host if the response carries the `x-github-request-id` header [3](#0-2) . The `wwwauth` fast-path skips this verification entirely and is reachable before `isGitHubHost` is ever called (it's checked first, and only falls through to `isGitHubHost` if no matching header is present).

Because `isDotCom`/`isGHE` are the only genuinely hostname-derived checks (`api.github.com`/`github.com`, or `*.ghe.com`) [4](#0-3) , and those fail for an arbitrary attacker domain, the `wwwauth` branch is the deciding factor for classifying an arbitrary attacker-controlled endpoint as `'enterprise'`.

Once classified as non-`'generic'`, and no existing account matches, `getCredential()` invokes the GitHub sign-in prompt against that arbitrary endpoint: [5](#0-4) 

`promptForGitHubSignIn` then binds the sign-in flow's target endpoint to the attacker's own `origin`: [6](#0-5) 

`setSignInEndpoint`/`beginEnterpriseSignIn` only validates that the endpoint is syntactically an `https://` URL — it performs no cryptographic or API-based confirmation that the target is actually a GitHub Enterprise instance [7](#0-6) .

### Impact Explanation
An attacker who controls a git remote (or a MITM/compromised proxy sitting in front of one — both are in-scope per the "attacker controls ... a git remote/proxy response" criterion) can, with a single crafted `401 WWW-Authenticate: Basic realm="GitHub"` response, cause GitHub Desktop to:
1. Pop the trusted-looking "Sign in to GitHub Enterprise" dialog unprompted, pre-filled with the attacker's server as the "GitHub Enterprise" endpoint.
2. Drive the user through an OAuth/enterprise-authentication flow whose target API/HTML URLs are derived entirely from the attacker's `origin`, since the real check (`isGitHubHost`'s `/meta` verification) is bypassed by the `wwwauth` shortcut.

If the user completes this flow believing Desktop verified the server as GitHub (as it normally does via the real hostname/`x-github-request-id` checks), enterprise credentials/OAuth tokens can end up being exchanged with attacker infrastructure instead of a real GitHub host — matching the report's "unauthorized OAuth ... credential/token exfiltration" impact category.

### Likelihood Explanation
No local access, admin rights, or pre-existing malware is required — only that the victim performs a normal git network operation (clone/fetch/push/pull) against a server the attacker controls or intercepts, which is a completely ordinary Desktop workflow. Crafting the `WWW-Authenticate` header is trivial and requires no certificate manipulation, unlike the DNS/TLS spoofing that would otherwise be needed to fool `isDotCom`/`isGHE`/`isGitHubHost`.

### Recommendation
Do not trust the `WWW-Authenticate` realm string as authoritative proof of GitHub identity. Either remove the `wwwauth` fast-path entirely and always fall back to the network-verified `isGitHubHost()` check, or use the realm hint only to prioritize which verification to attempt first while still requiring the `/meta` + `x-github-request-id` confirmation (or a match against an already-known, user-configured Enterprise endpoint) before classifying an endpoint as `'enterprise'` and initiating a sign-in flow bound to that host.

### Proof of Concept
1. Attacker stands up an HTTP git server (or a MITM proxy) at `https://evil.example.com/victim/repo.git`.
2. Victim adds this as a remote and runs `fetch`/`clone` in GitHub Desktop.
3. The server responds to the credential request with:
   ```
   HTTP/1.1 401 Unauthorized
   WWW-Authenticate: Basic realm="GitHub"
   ```
4. Git forwards this to Desktop's trampoline credential helper as `wwwauth[0]=Basic realm="GitHub"`.
5. `getEndpointKind()` matches `/realm="GitHub"/` and returns `'enterprise'` [8](#0-7) , without any request ever being made to confirm the host is really GitHub.
6. `getCredential()` sees no existing account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com/...')` [9](#0-8) .
7. `promptForGitHubSignIn` calls `beginEnterpriseSignIn` + `setSignInEndpoint(origin)` with `origin === 'https://evil.example.com'` [10](#0-9) , launching the enterprise sign-in dialog against the attacker's host.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-125)
```typescript
  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
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
}
```

**File:** app/src/lib/api.ts (L2429-2491)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

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

**File:** app/src/lib/endpoint-capabilities.ts (L47-62)
```typescript
export const isDotCom = (ep: string) => {
  if (ep === getDotComAPIEndpoint()) {
    return true
  }

  const { hostname } = new URL(ep)
  return hostname === 'api.github.com' || hostname === 'github.com'
}

export const isGist = (ep: string) => {
  const { hostname } = new URL(ep)
  return hostname === 'gist.github.com' || hostname === 'gist.ghe.io'
}

/** Whether or not the given endpoint URI is under the ghe.com domain */
export const isGHE = (ep: string) => new URL(ep).hostname.endsWith('.ghe.com')
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-99)
```typescript
  public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
    return new Promise<Account | undefined>(async resolve => {
      const cb = (result: SignInResult) => {
        resolve(result.kind === 'success' ? result.account : undefined)
        this.dispatcher.closePopup(PopupType.SignIn)
      }

      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }

      this.dispatcher.showPopup({
        type: PopupType.SignIn,
        isCredentialHelperSignIn: true,
        credentialHelperUrl: endpoint,
      })
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
