### Title
Attacker-controlled `WWW-Authenticate` response from a git remote silently triggers an Enterprise OAuth flow and leaks Desktop's OAuth `client_secret` to that remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Prisma report's core defect is a *mutable trust anchor*: `troveManagerImpl` can be changed after clones are made, so downstream code (`getTroveManager`/`predictDeterministicAddress`) keeps trusting a value that no longer represents the real deployed contract. The Desktop analog is the credential-helper's endpoint-classification logic, which derives a security-sensitive trust decision ("is this endpoint a GitHub host?") from a value that is *entirely controlled by the remote party* — the git `WWW-Authenticate` header — and then uses that classification to drive a real OAuth exchange, including sending the app's `client_secret`, to whatever host produced that header.

### Finding Description
`getEndpointKind` in [1](#0-0)  classifies an unknown git remote as `'enterprise'` purely because it captured a `wwwauth[...]` credential field whose value contains `realm="GitHub"`. This value comes straight from the HTTP `WWW-Authenticate` header returned by the remote server, which git forwards verbatim to Desktop's credential helper — it is not verified against any real GitHub/GHE identity check (the actual network probe `isGitHubHost` is only reached as a last resort, after the header-based "happy path" already short-circuited the decision).

Once classified as `'enterprise'` and lacking a matching account, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` [2](#0-1) , which begins an Enterprise sign-in flow and sets the sign-in endpoint to the *attacker's own origin*: [3](#0-2) .

From there, the normal Enterprise OAuth code path executes against that attacker-controlled origin. `getOAuthAuthorizationURL` builds the authorize URL from the (attacker) endpoint, and `requestOAuthToken` POSTs to `{endpoint}/login/oauth/access_token` including Desktop's hard-coded `client_id`/`client_secret`: [4](#0-3) . This flow is invoked from `SignInStore.resolveOAuthRequest`/`setEndpoint` [5](#0-4) , none of which validate that `endpoint` is an actual GitHub Enterprise host — `setEndpoint` only checks URL syntax/HTTPS, not identity.

No existing guard stops this: `getGitHubCredential`/`findGitHubTrampolineAccount` only match against *already known* accounts by origin [6](#0-5) ; for a brand-new, unknown host the only "GitHub-ness" signal used before falling back to a network probe is the self-reported `WWW-Authenticate` realm string.

### Impact Explanation
Because Desktop's OAuth `client_id`/`client_secret` are shared, hard-coded application secrets (not per-user), sending `client_secret` to an attacker-controlled host is a genuine secret exfiltration: the attacker's server receives Desktop's real OAuth application secret in the `access_token` POST body, which can then be used to abuse Desktop's OAuth app identity (e.g., forging authorization code exchanges against real github.com if `client_id`/`client_secret` are reused across environments, or otherwise compromising the app's OAuth integration). It also causes the user to be shown Desktop's own trusted "Sign in with GitHub Enterprise" dialog and "Sign in using your browser" button while the browser is actually being pointed at the attacker's `/login/oauth/authorize` endpoint — an in-product phishing primitive driven purely by an HTTP response header from a remote the user is fetching from, not by any URL the user typed.

### Likelihood Explanation
The trigger requires nothing more than the victim performing an ordinary `fetch`/`clone`/`push` against a remote that the attacker controls or has compromised (satisfies "attacker controls ... a git remote/proxy response"). The attacker only needs to serve a `401` with `WWW-Authenticate: Basic realm="GitHub"` for an unrecognized host; this is fully within the attacker's control and requires no cooperation from GitHub or any special privileges. The remaining step — the victim clicking the pre-existing "Sign in using your browser" button inside Desktop's own dialog — is a normal, expected interaction with the app's own UI, not an unnatural or out-of-band step.

### Recommendation
Do not classify an unknown endpoint as `'enterprise'` (or GitHub-affiliated) based solely on the self-reported `WWW-Authenticate` realm string. Require the `isGitHubHost(endpoint)` network verification (or equivalent authenticated check) before offering the GitHub/Enterprise sign-in UI or initiating any OAuth token exchange, and independently confirm the endpoint's identity again immediately before sending `client_id`/`client_secret` in `requestOAuthToken`, rejecting hosts that don't match a previously verified GitHub Enterprise instance the user explicitly registered.

### Proof of Concept
1. Attacker sets up an HTTP git server at `https://evil.example` that responds to unauthenticated Git-over-HTTP requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds/fetches from `https://evil.example/foo.git` in GitHub Desktop (e.g. clones a repo whose remote was retargeted, or simply adds this remote).
3. Git invokes Desktop's credential helper trampoline; `getCredentialUrl`/`getEndpointKind` see the `wwwauth[...]` field containing `realm="GitHub"` and return `'enterprise'` (`app/src/lib/trampoline/trampoline-credential-helper.ts:153-165`).
4. No account matches `getAPIEndpoint('https://evil.example')`, so `ui.promptForGitHubSignIn('https://evil.example')` is invoked (`trampoline-credential-helper.ts:109-125`), which calls `beginEnterpriseSignIn` and `setSignInEndpoint('https://evil.example')` (`trampoline-ui-helper.ts:80-104`).
5. Victim clicks "Sign in using your browser" in the resulting dialog; Desktop opens `https://evil.example/login/oauth/authorize?client_id=...` (`api.ts:2357-2368`).
6. Attacker's server redirects back via the `x-github-client://oauth` deep link with an arbitrary `code`/`state`; `SignInStore.resolveOAuthRequest` calls `requestOAuthToken('https://evil.example', code)`, which POSTs `client_id` and Desktop's real `client_secret` to `https://evil.example/login/oauth/access_token` (`api.ts:2370-2396`, `sign-in-store.ts:332-358`) — exfiltrating the secret to the attacker's server.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-165)
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
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-104)
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
    }).catch(e => {
      log.error(`Could not prompt for GitHub sign in`, e)
      return undefined
    })
  }
```

**File:** app/src/lib/api.ts (L2357-2396)
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

**File:** app/src/lib/stores/sign-in-store.ts (L332-459)
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

  /**
   * Initiate a sign in flow for a GitHub Enterprise instance.
   * This will put the store in the EndpointEntry step ready to
   * receive the url to the enterprise instance.
   */
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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
