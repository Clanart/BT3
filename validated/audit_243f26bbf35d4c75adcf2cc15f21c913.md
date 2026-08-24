### Title
`GolomTrader`-style trust-without-verification bypass in git credential helper's `getEndpointKind` — spoofable `WWW-Authenticate` header misclassifies attacker host as GitHub Enterprise - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The reported bug is a broken-invariant pattern: a security decision is gated on a raw, unauthenticated signal (`ecrecover`'s return value) instead of a properly verified one, and the "invalid" sentinel (`0`/signer-zero) is trusted as if it were valid. GitHub Desktop's git-credential trampoline has the same shape of bug: `getEndpointKind` decides whether a remote host is "GitHub Enterprise" by doing a plain substring match on a `WWW-Authenticate` header value that is fully attacker-controlled (it comes straight from the remote server's HTTP response during a `git fetch`/`push`/`clone`), with no cryptographic or network-verified check backing that decision. [1](#0-0) 

### Finding Description
When Git needs credentials for a host, it captures any `WWW-Authenticate` response headers from the server and forwards them to Desktop's credential helper trampoline as `wwwauth[...]` fields on the credential map: [1](#0-0) 

`getEndpointKind` uses this attacker-supplied text as a trust anchor:
```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
```
This is a plain string match on data returned by whatever server the user is talking to — a malicious/compromised git remote, or a MITM proxy sitting in front of an internal git host, can simply return `WWW-Authenticate: Basic realm="GitHub"` on a 401 and be classified as `'enterprise'`, i.e. "this is a GitHub host", with none of the verification that the code applies elsewhere for the same purpose (the `isGitHubHost` fallback path actually performs a `fetch` to `/meta` and checks for the `x-github-request-id` response header before concluding a host is GitHub-flavored): [2](#0-1) 

That fallback (real) verification is only reached if the spoofable header match fails — the vulnerable branch short-circuits and skips it entirely, exactly like `ecrecover` returning `0` and the caller failing to reject that sentinel.

The `'enterprise'` classification then drives real behavior in `getCredential`: [3](#0-2) 
If no account is already registered for that exact endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` using the **attacker-controlled endpoint string** taken directly from the git remote URL, not from any verified GitHub identity.

If the user proceeds with what looks like a normal "sign in to your GitHub Enterprise instance" prompt, the resulting Enterprise sign-in flow builds the OAuth authorization/token URLs directly from that attacker-supplied endpoint: [4](#0-3) 
and the token exchange step POSTs the app's hardcoded `ClientID`/`ClientSecret` and the OAuth `code` to `urlBase`, which is derived from the same attacker-controlled endpoint: [5](#0-4) 

Because `getHTMLURL`/`getOAuthAuthorizationURL`/`requestOAuthToken` all key off the endpoint value that originated from the spoofed `WWW-Authenticate` realm, the attacker's server — not github.com/GHES — ends up receiving the app's OAuth `client_secret` and authorization code in the token-exchange POST.

### Impact Explanation
This lets an attacker who controls a git remote (or sits as a proxy/MITM in front of one) trick Desktop into treating an arbitrary host as a trusted GitHub Enterprise endpoint purely by returning a crafted `WWW-Authenticate` header, with no TLS/identity verification. The concrete corrupted value is the derived `endpoint`/`urlBase` used for the OAuth token exchange, which should only ever point at a genuinely verified GitHub host. Consequences:
- The app's embedded OAuth `ClientSecret` and a live authorization `code` are sent to the attacker's server (credential/secret exfiltration).
- The user is shown a "GitHub sign-in" UI for a host that isn't actually GitHub, enabling further phishing/credential harvesting under a false trust signal supplied by Desktop itself (unauthorized OAuth/account-binding flow).

This matches the report's core bug class: a verification function silently accepts an attacker-influenced "invalid"/unverifiable signal as proof of a security-relevant fact, bypassing the real check (`isGitHubHost`'s network probe) that exists specifically to prevent this.

### Likelihood Explanation
The trigger requires only that the victim add/clone from a remote controlled by, or proxied by, the attacker and attempt an authenticated git operation against it — no local access, no admin rights, and no prior compromise of the host are needed. Returning a custom `WWW-Authenticate` header on a 401 response is a two-line change to any HTTP server the attacker operates, making this trivially reproducible. The remaining step (user clicking through the sign-in prompt) is a normal, expected interaction rather than an "unnatural" user action, since Desktop itself is what surfaces the (fake) GitHub Enterprise sign-in flow.

### Recommendation
Do not use the `WWW-Authenticate` realm string as a positive trust signal for classifying a host as GitHub/GHE. At minimum:
- Remove the `wwwauth[...]` substring-match branch from `getEndpointKind`, or only use it as a hint to *skip straight to* the already-existing verified check (`isGitHubHost`'s `/meta` probe with `x-github-request-id`), never bypassing it.
- Before initiating any Enterprise OAuth flow (`beginEnterpriseSignIn`/`requestOAuthToken`) from a credential-helper-triggered prompt, re-verify the endpoint via `isGitHubHost`/`supportsRepoRules`-style network check rather than trusting a header value asserted by the remote itself.

### Proof of Concept
1. Attacker sets up an HTTPS git server (e.g., a rogue remote or MITM proxy) at `https://evil.example.com/some/repo.git` that responds `401` to unauthenticated `git-upload-pack`/`git-receive-pack` requests with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this remote in Desktop and performs a fetch/push. Git invokes the trampoline credential helper's `get` command, forwarding the header as `wwwauth[...]=Basic realm="GitHub"` in the credential map.
3. `getEndpointKind` matches `v.includes('realm="GitHub"')` and returns `'enterprise'` for `evil.example.com`, without any network verification. [1](#0-0) 
4. `getCredential` finds no existing account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')`. [6](#0-5) 
5. If the victim proceeds, the Enterprise sign-in flow performs OAuth against `evil.example.com`, and `requestOAuthToken` POSTs `client_id`, `client_secret`, and `code` to that attacker-controlled server. [5](#0-4)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-130)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
```typescript
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
```

**File:** app/src/lib/api.ts (L2370-2395)
```typescript
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

**File:** app/src/lib/stores/sign-in-store.ts (L366-459)
```typescript
  public beginEnterpriseSignIn(
    resultCallback?: (result: SignInResult) => void
  ) {
    if (this.state !== null) {
      this.reset()
    }

    this.setState({
      kind: SignInStep.EndpointEntry,
      error: null,
      loading: false,
      resultCallback: resultCallback ?? noop,
    })
  }

  /**
   * Attempt to advance from the EndpointEntry step with the given endpoint
   * url. This method must only be called when the store is in the authentication
   * step or an error will be thrown.
   *
   * The provided endpoint url will be validated for syntactic correctness as
   * well as connectivity before the promise resolves. If the endpoint url is
   * invalid or the host can't be reached the promise will be rejected and the
   * sign in state updated with an error to be presented to the user.
   *
   * If validation is successful the store will advance to the authentication
   * step.
   */
  public async setEndpoint(url: string): Promise<void> {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.EndpointEntry &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with endpoint entry`
      )
    }

    /**
     * If the user enters a github.com url in the GitHub Enterprise sign-in
     * flow we'll redirect them to the GitHub.com sign-in flow.
     */
    if (/^(?:https:\/\/)?(?:api\.)?github\.com($|\/)/.test(url)) {
      this.beginDotComSignIn(currentState.resultCallback)
      return
    }

    this.setState({ ...currentState, loading: true })

    let validUrl: string
    try {
      validUrl = validateURL(url)
    } catch (e) {
      let error = e
      if (e.name === InvalidURLErrorName) {
        error = new Error(
          `The GitHub Enterprise instance address doesn't appear to be a valid URL. We're expecting something like https://example.ghe.com.`
        )
      } else if (e.name === InvalidProtocolErrorName) {
        error = new Error(
          'Unsupported protocol. Only https is supported when authenticating with GitHub Enterprise instances.'
        )
      }

      this.setState({ ...currentState, loading: false, error })
      return
    }

    const endpoint = getEnterpriseAPIURL(validUrl)

    const existingAccount = this.accounts.find(x => x.endpoint === endpoint)

    if (existingAccount) {
      this.setState({
        kind: SignInStep.ExistingAccountWarning,
        endpoint,
        existingAccount,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    } else {
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    }
  }
```
