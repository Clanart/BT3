Confirmed: `validateURL` in `app/src/ui/lib/enterprise-validate-url.ts:14-45` only checks for `https:` protocol and does zero hostname allow-listing, and the app-wide OAuth `__OAUTH_SECRET__` (`app/app-info.ts:6,20-22`) is compiled into every Desktop binary and reused for every enterprise/GitHub.com OAuth exchange. Combined with the broken host-classification heuristic in `isGitHubHost`, this lets an attacker-controlled git host be silently treated as a trusted GitHub Enterprise endpoint and receive the shared OAuth `client_secret` plus drive the sign-in UI.

### Title
Overly-permissive `isGitHubHost` heuristic lets an attacker-controlled git remote be treated as a trusted GitHub Enterprise endpoint, leaking the shared OAuth client secret and hijacking account sign-in - (File: app/src/lib/api.ts)

### Summary
The bug report's pattern ("a critical trust-establishing value can be set/derived without proper validation, permanently breaking an access-control invariant") maps onto Desktop's endpoint-classification logic. `isGitHubHost()` uses a naive regex that classifies any hostname containing `github.` as a trusted GitHub Enterprise host, with no check that it is actually a subdomain relationship or that it responds like a real GitHub Enterprise Server. A repository whose remote/submodule points to an attacker-registered host such as `github.attacker.com` gets misclassified as `enterprise`, which drives Desktop's git credential trampoline into the enterprise sign-in flow against that attacker host — sending it the app's embedded OAuth `client_id`/`client_secret` during token exchange and letting it return an arbitrary account to be stored/used by Desktop.

### Finding Description
`isGitHubHost` in [1](#0-0)  does:
```
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```
For a hostname like `github.attacker.com`, `(^|\.)(github)\.` matches at the start of the string (`^github.`), so the function returns `true` — fully attacker-controlled infrastructure is classified as "github" without any further verification (the network probe against `/meta` that would normally validate GHES headers is bypassed entirely by this early return).

This classification is consumed by the git credential trampoline's `getEndpointKind` at [2](#0-1) , which also short-circuits to `'enterprise'` if the remote server simply returns a `WWW-Authenticate: ...realm="GitHub"...` header (lines 153-165) — something a malicious server fully controls. Either path is reachable purely by the user cloning/fetching a repository (or a submodule) whose remote points to the attacker's host; no unnatural steps are required.

In `getCredential` at [3](#0-2) , once `endpointKind !== 'generic'` and no existing account matches, Desktop calls `ui.promptForGitHubSignIn(endpoint)` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-104`), which invokes `dispatcher.beginEnterpriseSignIn` and `setSignInEndpoint(origin)` with `origin` being the attacker's host, then shows the standard "Sign in" popup. The user, believing they are authenticating a legitimate GitHub Enterprise remote (Desktop itself vouched for this classification), proceeds.

`SignInStore.setEndpoint()` ( [4](#0-3) ) calls `validateURL()`, which as shown in [5](#0-4)  only enforces `https:` — it performs **no hostname validation** and would readily accept `https://github.attacker.com`. `authenticateWithBrowser()` ( [6](#0-5) ) then opens `getOAuthAuthorizationURL(endpoint, csrfToken)` in the system browser — a URL rooted at the attacker's server (`app/src/lib/api.ts:2357-2368`). When the deep-link callback returns a `code`, `resolveOAuthRequest` calls `requestOAuthToken(endpoint, code)` ( [7](#0-6) ), which POSTs `client_id`, **`client_secret`**, and `code` to `${attacker-host}/login/oauth/access_token`. The `client_secret` is the single shared `__OAUTH_SECRET__` compiled into every user's Desktop binary ( [8](#0-7) ), so it is sent, in the clear, to fully attacker-controlled infrastructure. The attacker's server can then return any forged `access_token`, which Desktop uses via `fetchUser(endpoint, token)` to fabricate an "authenticated" `Account` that gets persisted into `AccountsStore` (`app/src/lib/stores/accounts-store.ts:95-126`) with attacker-supplied `login`, `token`, `id`, etc.

### Impact Explanation
- **Credential exfiltration**: The application-wide OAuth `client_secret` is sent to an attacker-controlled server, letting the attacker impersonate the Desktop OAuth app going forward (e.g., forge authorize/callback flows or attempt token exchanges against real GitHub Enterprise or GitHub.com endpoints depending on how the OAuth app is scoped).
- **Unauthorized account binding**: Desktop can be tricked into creating/storing an "authenticated" account object supplied entirely by the attacker, which is subsequently used for auto-filled Git credentials against endpoints matching that origin (`findGitHubTrampolineAccount` matches by origin), and is surfaced in Desktop's account UI.
- The trigger is a hostname the attacker fully owns (`github.<attacker-domain>` or any of the WWW-Authenticate-based bypasses), which is exactly the "attacker controls a git remote/proxy response" primitive called out as valid impact.

### Likelihood Explanation
Reasonably likely: registering a `github.` subdomain is trivial and requires no interaction with GitHub's real infrastructure. The only user action needed is cloning/fetching from that remote (or having it introduced via a submodule of an otherwise legitimate-looking repo) and completing the sign-in prompt Desktop itself presents as trustworthy — this is normal Desktop usage, not an "unnatural" step. The existing regex and header-based heuristics were clearly meant as a lightweight fast-path before the real `/meta` network probe, but they fully bypass that probe instead of just skipping it as an optimization hint.

### Recommendation
- Remove or tighten the regex fast-path in `isGitHubHost` (`app/src/lib/api.ts:2452-2454`) — it should not treat arbitrary hostnames containing "github." as trusted; require an exact match or a known-suffix allowlist, and always fall back to the authenticated `/meta` probe for unknown hosts.
- Do not let the WWW-Authenticate `realm="GitHub"` header alone (attacker-controlled) short-circuit `getEndpointKind` to `'enterprise'` without corroboration.
- Add explicit hostname allow-listing/confirmation in `validateURL` (or in the enterprise sign-in flow) so a host is never silently treated as GitHub Enterprise based on heuristics an attacker can fully control.
- Consider not sending the shared `client_secret` to non-verified hosts, or use a per-endpoint verification step prior to any token exchange.

### Proof of Concept
1. Attacker stands up `https://github.attacker.com` with a valid TLS cert, serving a git repository (or a repo containing a submodule pointing at that host).
2. Victim clones/fetches this repository in GitHub Desktop.
3. Git's credential helper is invoked; the trampoline's `getEndpointKind` classifies `github.attacker.com` as `'enterprise'` either via the `isGitHubHost` regex fast-path (`/(^|\.)(github)\./`) or via a spoofed `WWW-Authenticate: realm="GitHub"` header from the attacker's server.
4. Since no existing account matches, Desktop calls `promptForGitHubSignIn('https://github.attacker.com')`, showing a normal-looking "Sign in" dialog for what appears to be a GitHub Enterprise instance.
5. Victim clicks "Continue With Browser". `validateURL` accepts the attacker's URL (only checks for `https:`). `authenticateWithBrowser` opens `https://github.attacker.com/login/oauth/authorize?client_id=...` in the system browser.
6. Attacker's fake authorize page redirects back to the Desktop protocol handler with an arbitrary `code`.
7. Desktop's `resolveOAuthRequest` calls `requestOAuthToken('https://github.attacker.com', code)`, POSTing `client_id`, `client_secret` (the shared secret embedded in the app), and `code` to the attacker's server — exfiltrating the secret.
8. Attacker's server returns a forged `access_token`; Desktop calls `fetchUser` against the attacker's host and stores whatever account data is returned as an authenticated Desktop account.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-125)
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

**File:** app/src/lib/stores/sign-in-store.ts (L260-330)
```typescript
  public async authenticateWithBrowser() {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.Authentication &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with browser authentication`
      )
    }

    this.setState({ ...currentState, loading: true })

    if (currentState.kind === SignInStep.ExistingAccountWarning) {
      const { existingAccount } = currentState
      // Try to avoid emitting an error out of AccountsStore if the account
      // is already gone.
      if (this.accounts.find(x => x.endpoint === existingAccount.endpoint)) {
        await this.accountStore.removeAccount(existingAccount)
      }
    }

    const csrfToken = crypto.randomUUID()

    new Promise<Account>((resolve, reject) => {
      const { endpoint, resultCallback } = currentState
      log.info('[SignInStore] initializing OAuth flow')
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        resultCallback,
        error: null,
        loading: true,
        oauthState: {
          state: csrfToken,
          endpoint,
          onAuthCompleted: resolve,
          onAuthError: reject,
        },
      })
      shell.openExternal(getOAuthAuthorizationURL(endpoint, csrfToken))
    })
      .then(account => {
        if (!this.state || this.state.kind !== SignInStep.Authentication) {
          // Looks like the sign in flow has been aborted
          log.warn('[SignInStore] account resolved but session has changed')
          return
        }

        log.info('[SignInStore] account resolved')
        this.emitAuthenticate(account)
        this.setState({
          kind: SignInStep.Success,
          resultCallback: this.state.resultCallback,
        })
      })
      .catch(e => {
        // Make sure we're still in the same sign in session
        if (
          this.state?.kind === SignInStep.Authentication &&
          this.state.oauthState?.state === csrfToken
        ) {
          log.info('[SignInStore] error with OAuth flow', e)
          this.setState({ ...this.state, error: e, loading: false })
        } else {
          log.info(`[SignInStore] OAuth error but session has changed: ${e}`)
        }
      })
  }
```

**File:** app/src/lib/stores/sign-in-store.ts (L394-437)
```typescript
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

**File:** app/app-info.ts (L19-22)
```typescript
    __OAUTH_CLIENT_ID__: s(process.env.DESKTOP_OAUTH_CLIENT_ID || devClientId),
    __OAUTH_SECRET__: s(
      process.env.DESKTOP_OAUTH_CLIENT_SECRET || devClientSecret
    ),
```
