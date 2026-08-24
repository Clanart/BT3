### Title
Malicious remote hostname spoofs a trusted "GitHub Enterprise" identity and triggers an unauthorized OAuth sign‑in prompt - ([File: app/src/lib/api.ts])

### Summary
The reported bug is a "trust decision made by a naive pattern/condition check that silently breaks (or misapplies) a security-relevant classification." In the ZkSync report, `TimestampAsserterLocator.locate` hardcodes a chain-ID check that wrongly disables a security guarantee. The GitHub Desktop analog is `isGitHubHost` in [1](#0-0) , which uses a naive regular expression, `/(^|\.)(github)\./`, to decide whether an arbitrary git remote hostname should be trusted as a "GitHub"/"enterprise" endpoint. This broken invariant lets an attacker-controlled hostname such as `ci.github.attacker.tld` be misclassified as a genuine GitHub Enterprise host, because the regex only requires the literal substring `github.` preceded by a dot or string start, not an actual GitHub-operated domain.

### Finding Description
`isGitHubHost(url)` is the function GitHub Desktop uses to decide, for an arbitrary git remote, whether it should be treated as a GitHub endpoint (dotcom, `ghe.com`, or "enterprise"): [1](#0-0) 

```
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```

This check is only correct for the intended example (`github.example.com`), but it also matches any hostname containing `.github.` as a label anywhere, e.g. `something.github.attacker.tld` or `ci.github.evil.io`. An attacker who controls such a domain (trivial — subdomains under an attacker-owned TLD/domain are free to register) can craft a git remote/submodule/LFS URL that git will contact during clone/fetch/push.

This classification feeds directly into the trampoline git-credential-helper's `getEndpointKind`, which is invoked whenever Git needs credentials for a URL: [2](#0-1) 

Once `isGitHubHost` returns `true` for the spoofed host, `getEndpointKind` returns `'enterprise'` rather than `'generic'`. Back in `getCredential`, because the endpoint is not `'generic'` and no existing account matches this new endpoint, Desktop automatically prompts the user to sign in to what it believes is a GitHub Enterprise instance: [3](#0-2) 

That prompt is rendered by `promptForGitHubSignIn`, which — for any hostname other than literally `github.com` — starts the **Enterprise sign-in flow** and sets the sign-in endpoint to the attacker's origin: [4](#0-3) 

From there, the standard enterprise OAuth flow builds an authorization URL and later exchanges the code for a token directly against the attacker-controlled origin: [5](#0-4) 

Existing guards do not stop this path:
- `isDotCom`/`isGHE` only check for the literal `github.com`/`api.github.com` hostnames or a `.ghe.com` suffix, so they don't reject the spoofed host: [6](#0-5) 
- `isKnownThirdPartyHost` is only a denylist of specific known non-GitHub SaaS domains (`gitlab.com`, `bitbucket.org`, etc.) and does not protect against an unknown attacker domain: [7](#0-6) 
- The enterprise sign-in URL validator only checks that the URL is syntactically valid HTTPS; it performs no verification that the target is actually a GitHub-operated server: [8](#0-7) 
- `same-origin-filter.ts` only prevents *redirect*-based cross-origin header leakage; it does nothing here because the request is made directly, on purpose, to the attacker's origin (that is the whole point of the flawed classification): [9](#0-8) 

The corrupted value is the boolean trust decision returned by `isGitHubHost`/`getEndpointKind` — it should be `'generic'` for an unverified third-party host but instead becomes `'enterprise'`, which is treated the same as a real GitHub Enterprise server for the purposes of triggering an automatic sign-in prompt and subsequent OAuth code/token exchange with that origin.

### Impact Explanation
Impact is limited but real: it does not directly disclose the user's actual GitHub.com/GHE OAuth token (those remain scoped to their real endpoints via strict `endpoint ===` account lookups), but it causes GitHub Desktop to:
- Present an unsolicited "Sign in to GitHub Enterprise" prompt for an attacker-chosen, spoofed hostname, which is a strong social-engineering primitive normally not available to attackers who only control a git remote/submodule URL.
- Perform the enterprise OAuth authorize/token-exchange flow against the attacker's server, sending the app's embedded `ClientID`/`ClientSecret` to that server (see `requestOAuthToken`), and accepting whatever `access_token` the attacker's server returns.
- Result in Desktop creating and persisting a new "account" bound to the attacker's endpoint under the guise of a legitimate enterprise identity — an unauthorized OAuth/account-binding outcome consistent with the requested impact category.

### Likelihood Explanation
Likelihood is high for the trigger condition (any attacker who can get a victim to clone/fetch/add a remote or submodule pointing at a domain containing `.github.` as a label can reach this code path with no other user action beyond a normal git operation), but exploitation still requires the victim to interact with (complete) the resulting sign-in prompt, which somewhat lowers real-world severity while keeping it within the "attacker controls a git remote → triggers unwanted trust behavior" category allowed by the task.

### Recommendation
Do not use a substring/regex heuristic to establish trust for the "enterprise" classification. Restrict the `github.` heuristic (and any heuristic-based branch) to only ever *decrease* confidence (return `false`) or require it to be confirmed by the authenticated `/meta` HEAD request check (which requires an `x-github-request-id` response header) before ever exposing the Enterprise sign-in UI automatically. At minimum, gate the automatic `promptForGitHubSignIn` call so it only fires for domains verified by the network-based check, not by hostname pattern matching alone.

### Proof of Concept
1. Attacker registers/controls a domain such as `ci.github.attacker.tld` and stands up an HTTPS server there.
2. Attacker publishes a public repository whose `.gitmodules`/remote (or shares a clone URL) points to `https://ci.github.attacker.tld/foo/bar.git`.
3. Victim clones/fetches this repository in GitHub Desktop; Git invokes the credential helper for `https://ci.github.attacker.tld`.
4. `getEndpointKind` → `isGitHubHost` matches `/(^|\.)(github)\./` against `ci.github.attacker.tld` and returns `true`, classifying the endpoint as `'enterprise'`. [10](#0-9) 
5. `getCredential` finds no existing account for this endpoint and calls `ui.promptForGitHubSignIn`, which opens the Enterprise sign-in dialog pointed at the attacker's origin. [11](#0-10) 
6. If the victim proceeds, Desktop performs `getOAuthAuthorizationURL`/`requestOAuthToken` against the attacker's server, which can return an arbitrary `access_token`, and Desktop stores a new "GitHub Enterprise" account bound to the attacker's domain. [5](#0-4)

### Citations

**File:** app/src/lib/api.ts (L2357-2395)
```typescript
export function getOAuthAuthorizationURL(
  endpoint: string,
  state: string
): string {
  const urlBase = getHTMLURL(endpoint)
  const scope = encodeURIComponent(oauthScopes.join(' '))

  return new window.URL(
    `/login/oauth/authorize?client_id=${ClientID}&scope=${scope}&state=${state}`,
    urlBase
  ).toString()
}

export async function requestOAuthToken(
  endpoint: string,
  code: string
): Promise<string | null> {
  try {
    const urlBase = getHTMLURL(endpoint)
    const response = await request(
      urlBase,
      null,
      'POST',
      'login/oauth/access_token',
      {
        client_id: ClientID,
        client_secret: ClientSecret,
        code: code,
      }
    )
    tryUpdateEndpointVersionFromResponse(endpoint, response)

    const result = await parsedResponse<IAPIAccessToken>(response)
    return result.access_token
  } catch (e) {
    log.warn(`requestOAuthToken: failed with endpoint ${endpoint}`, e)
    return null
  }
}
```

**File:** app/src/lib/api.ts (L2407-2427)
```typescript
const knownThirdPartyHosts = new Set([
  'dev.azure.com',
  'gitlab.com',
  'bitbucket.org',
  'amazonaws.com',
  'visualstudio.com',
])

const isKnownThirdPartyHost = (hostname: string) => {
  if (knownThirdPartyHosts.has(hostname)) {
    return true
  }

  for (const knownHost of knownThirdPartyHosts) {
    if (hostname.endsWith(`.${knownHost}`)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/api.ts (L2429-2454)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-125)
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

**File:** app/src/main-process/same-origin-filter.ts (L1-52)
```typescript
import { OrderedWebRequest } from './ordered-webrequest'

/**
 * Installs a web request filter to prevent cross domain leaks of auth headers
 *
 * GitHub Desktop uses the fetch[1] web API for all of our API requests. When fetch
 * is used in a browser and it encounters an http redirect to another origin
 * domain CORS policies will apply to prevent submission of credentials[2].
 *
 * In our case however there's no concept of same-origin (and even if there were
 * it'd be problematic because we'd be making cross-origin request constantly to
 * GitHub.com and GHE instances) so the `credentials: same-origin` setting won't
 * help us.
 *
 * This is normally not a problem until http redirects get involved. When making
 * an authenticated request to an API endpoint which in turn issues a redirect
 * to another domain fetch will happily pass along our token to the second
 * domain and there's no way for us to prevent that from happening[3] using
 * the vanilla fetch API.
 *
 * That's the reason why this filter exists. It will look at all initiated
 * requests and store their origin along with their request ID. The request id
 * will be the same for any subsequent redirect requests but the urls will be
 * changing. Upon each request we will check to see if we've seen the request
 * id before and if so if the origin matches. If the origin doesn't match we'll
 * strip some potentially dangerous headers from the redirect request.
 *
 * 1. https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API
 * 2. https://fetch.spec.whatwg.org/#http-network-or-cache-fetch
 * 3. https://github.com/whatwg/fetch/issues/763
 *
 * @param orderedWebRequest
 */
export function installSameOriginFilter(orderedWebRequest: OrderedWebRequest) {
  // A map between the request ID and the _initial_ request origin
  const requestOrigin = new Map<number, string>()
  const safeProtocols = new Set(['devtools:', 'file:', 'chrome-extension:'])
  const unsafeHeaders = new Set(['authentication', 'authorization', 'cookie'])

  orderedWebRequest.onBeforeRequest.addEventListener(async details => {
    const { protocol, origin } = new URL(details.url)

    // This is called once for the initial request and then once for each
    // "subrequest" thereafter, i.e. a request to https://foo/bar which gets
    // redirected to https://foo/baz will trigger this twice and we only
    // care about capturing the initial request origin
    if (!safeProtocols.has(protocol) && !requestOrigin.has(details.id)) {
      requestOrigin.set(details.id, origin)
    }

    return {}
  })
```
