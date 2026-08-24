## Finding

### Title
Attacker-controlled `WWW-Authenticate` header silently upgrades an arbitrary git host to a trusted "GitHub Enterprise" identity, bypassing the real host-verification check and triggering an OAuth account-binding flow against the attacker's server - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The M-3 report's broken invariant is: *a security-relevant state transition trusts an unverifiable signal from an untrusted external party, and once that signal is accepted there is no independent check to catch the bad transition.* In Desktop's credential-helper trampoline, the classification of "is this git remote a GitHub host?" is decided in part by parsing the `WWW-Authenticate` header that the remote git server itself returns — content that is 100% attacker-controlled when the remote is an attacker-run/MITM'd git server — and this classification is used to short-circuit a stronger, network-verified check (`isGitHubHost`) that exists elsewhere in the codebase specifically to guard against this kind of spoofing.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts:137-179` is invoked whenever git needs credentials for an HTTP(S) remote (fetch/clone/push). Git forwards any `WWW-Authenticate` headers it received from the remote server as `wwwauth[]` fields in the credential-helper protocol payload. Desktop parses these directly and trusts them: [1](#0-0) 

```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
```

This returns `'enterprise'` **immediately**, before ever reaching the codebase's actual verification mechanism, `isGitHubHost()`, which performs a real network request and checks for the authentic `x-github-request-id` response header before concluding a host is GitHub-flavored: [2](#0-1) 

Because a git server the attacker controls (or a MITM'd HTTP proxy for a plain-HTTP remote) can freely set `WWW-Authenticate: Basic realm="GitHub"` on a 401 response, the attacker can force `getEndpointKind` to classify their own arbitrary host as `'enterprise'` without ever needing to pass the legitimate `x-github-request-id` check — the strong guard is simply never reached.

The consequence, back in `getCredential`: [3](#0-2) 

Since `endpointKind !== 'generic'` and no account is bound to `apiEndpoint = getEnterpriseAPIURL(attackerHost)`, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

This shows Desktop's standard "Sign in" dialog and, because `hostname !== 'github.com'`, drives the **Enterprise** sign-in flow with `origin` set to the attacker's own domain (`beginEnterpriseSignIn` + `setSignInEndpoint(origin)`). That flow subsequently calls `getOAuthAuthorizationURL`/`requestOAuthToken`, which POST Desktop's static `client_id`/`client_secret` directly to the attacker's server as part of the (attacker-controlled) OAuth code exchange: [5](#0-4) 

Because the entire OAuth token exchange terminates on the attacker's own server, the attacker can return any `access_token` and any user profile they like, and `fetchUser` will happily construct and the app will persist a brand-new `Account` bound to their endpoint: [6](#0-5) [7](#0-6) 

The corrupted value is `endpointKind` (and everything downstream that trusts it): a value that should only ever be derived from an independently verified fact ("this host really speaks the GitHub API") is instead derived from an unauthenticated header the remote server chose to send.

### Impact Explanation
- **Unauthorized OAuth/account binding**: an attacker-controlled git remote can cause Desktop to silently launch its Enterprise OAuth sign-in flow against the attacker's own domain and persist the resulting bogus `Account` in `AccountsStore`, entirely of the attacker's making (arbitrary login/user id/avatar/token, since the attacker's server issues the token itself).
- **Credential/secret exfiltration**: Desktop's OAuth `client_id`/`client_secret` are transmitted to the attacker-chosen host as part of the code-exchange request initiated by this flow.
- The existing hardening (`isGitHubHost`'s real network probe for `x-github-request-id`) exists precisely to prevent trusting a host's self-reported identity, but it is bypassed because the `wwwauth[]` short-circuit runs first and returns before `isGitHubHost` is ever consulted.

### Likelihood Explanation
Reaching this path only requires the user to perform an ordinary git operation (clone/fetch/push) against a URL under attacker control — e.g., a repository the user was linked to, added as a remote, or opened via the `openRepo` deep link handled in `app/src/lib/parse-app-url.ts`. No local access, no admin rights, and no prior compromise are required; the attacker only needs to run a git-HTTP-capable server that returns a crafted `WWW-Authenticate` header, which is trivial. The one non-automatic step is that the user must click through Desktop's own "Sign in" dialog that the app itself pops up as part of normal credential resolution — this is the app's expected UX for any new host requiring auth, so it is not an "unnatural" step, but it also gives the attacker a legitimate-looking cover story ("sign in to access this repository").

### Recommendation
- Do not let `wwwauth[]` header content alone decide `'enterprise'` classification. Always require the independent, network-verified `isGitHubHost()` check (or an equivalent cryptographic/API-level verification) before treating a host as GitHub-flavored, regardless of any headers the remote itself supplied.
- If `wwwauth[]` is kept as a fast-path heuristic, require it to be corroborated by an actual API round-trip to the endpoint (e.g., verifying `x-github-request-id`) before proceeding to the OAuth sign-in flow or persisting a new `Account`.
- Surface stronger UI cues (e.g., the actual hostname prominently, and an explicit warning) whenever an Enterprise sign-in flow is being auto-triggered as a side effect of a git credential request rather than by the user explicitly choosing "Add Enterprise account" from Preferences.

### Proof of Concept
1. Attacker stands up a git-over-HTTP server at `https://ci-tools.example.net/evil-repo.git` that, for any unauthenticated request, responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim, in GitHub Desktop, clones/fetches this URL (e.g. via `Clone repository` dialog, an added remote, or the `x-github-client://openRepo/https://ci-tools.example.net/evil-repo.git` deep link handled by `parseAppURL`/`dispatchURLAction`).
3. Git invokes Desktop's credential helper trampoline; the forwarded `wwwauth[0]=Basic realm="GitHub"` line causes `getEndpointKind` to return `'enterprise'` without any real verification (`app/src/lib/trampoline/trampoline-credential-helper.ts:153-165`).
4. Since no account matches `apiEndpoint`, `ui.promptForGitHubSignIn('https://ci-tools.example.net/...')` fires, showing Desktop's normal "Sign in" dialog and beginning an Enterprise sign-in against the attacker's origin.
5. If the victim proceeds, Desktop's OAuth exchange (`requestOAuthToken`) POSTs `client_id`/`client_secret`/`code` to the attacker's server (`app/src/lib/api.ts:2370-2395`); the attacker's server returns an arbitrary `access_token`.
6. `fetchUser` is called against the attacker's endpoint and returns whatever the attacker's fake `/user` endpoint provides; the resulting bogus `Account` is stored by `SignInStore`/`AccountsStore` as a legitimate signed-in account bound to the attacker's domain.

Note: I could not fully trace how this persisted attacker-controlled `Account` is subsequently surfaced/used elsewhere in the UI (e.g., in repository publishing or account switchers) within the indexed portion of the codebase, so the downstream blast radius of the bogus account is based on the general role of `AccountsStore` entries rather than a step-by-step trace of every consumer.

### Citations

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

**File:** app/src/lib/api.ts (L2233-2264)
```typescript
/** Fetch the user authenticated by the token. */
export async function fetchUser(
  endpoint: string,
  token: string
): Promise<Account> {
  const api = new API(endpoint, token)
  try {
    const [user, emails, copilotInfo, features] = await Promise.all([
      api.fetchAccount(),
      api.fetchEmails(),
      api.fetchUserCopilotInfo(),
      api.fetchFeatureFlags(),
    ])

    return new Account(
      user.login,
      endpoint,
      token,
      emails,
      user.avatar_url,
      user.id,
      user.name || user.login,
      user.plan?.name,
      copilotInfo?.copilotEndpoint,
      copilotInfo?.isCopilotDesktopEnabled,
      features,
      copilotInfo?.copilotLicenseType
    )
  } catch (e) {
    log.warn(`fetchUser: failed with endpoint ${endpoint}`, e)
    throw e
  }
```

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

**File:** app/src/lib/api.ts (L2465-2484)
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

**File:** app/src/lib/stores/sign-in-store.ts (L332-359)
```typescript
  public async resolveOAuthRequest(action: IOAuthAction) {
    if (!this.state || this.state.kind !== SignInStep.Authentication) {
      return
    }

    if (!this.state.oauthState) {
      return
    }

    if (this.state.oauthState.state !== action.state) {
      log.warn(
        'requestAuthenticatedUser was not called with valid OAuth state. This is likely due to a browser reloading the callback URL. Contact GitHub Support if you believe this is an error'
      )
      return
    }

    const { endpoint } = this.state
    const token = await requestOAuthToken(endpoint, action.code)

    if (token) {
      const account = await fetchUser(endpoint, token)
      this.state.oauthState.onAuthCompleted(account)
    } else {
      this.state.oauthState.onAuthError(
        new Error('Failed retrieving authenticated user')
      )
    }
  }
```
