This confirms the vulnerability path. `promptForGitHubSignIn` calls `dispatcher.setSignInEndpoint(origin)` with the attacker-supplied `endpoint` derived directly from a fetch/clone request's credential URL, with no restriction that it must be a known/pre-approved GitHub Enterprise host. That endpoint feeds into `setEndpoint()`, which calls `requestOAuthToken()`/`getOAuthAuthorizationURL()`, both of which send `ClientID`/`ClientSecret` to whatever host was supplied.

### Title
OAuth client secret can be exfiltrated to attacker-controlled host via forged `WWW-Authenticate: realm="GitHub"` header during git fetch/clone - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
When Git performs an HTTP(S) operation against a remote (a clone, fetch, push, or submodule update), Desktop's credential-helper trampoline classifies the target host as GitHub, GitHub Enterprise, or "generic" in `getEndpointKind()`. If the server (fully attacker-controlled, since the attacker only needs to control a remote/proxy the victim fetches from) returns an HTTP `WWW-Authenticate` header containing `realm="GitHub"`, the host is classified as `'enterprise'` — with **no verification that the host is actually a real GHE instance**. [1](#0-0) 

If Desktop has no account already registered for that exact endpoint, `getCredential()` calls `ui.promptForGitHubSignIn(endpoint)` using the attacker's arbitrary hostname. [2](#0-1) 

`promptForGitHubSignIn` then feeds that same attacker-controlled `origin` straight into `dispatcher.beginEnterpriseSignIn` + `dispatcher.setSignInEndpoint(origin)`, with no allow-listing of legitimate GHE domains. [3](#0-2) 

`setEndpoint()` in `sign-in-store.ts` only validates that the URL is syntactically valid HTTPS (`validateURL`) — it does not check the host against any known/trusted list — and then proceeds to the Authentication step for that endpoint. [4](#0-3) 

If the user (believing this is a legitimate "sign in to your GitHub Enterprise" prompt triggered by their own git operation) completes the OAuth browser flow, `requestOAuthToken()` performs a `POST` to `${endpoint}/login/oauth/access_token` including the application's `client_id` **and `client_secret`**. [5](#0-4) 

Because `endpoint` is attacker-controlled, `ClientSecret` (a static, shared application secret embedded in the Desktop binary) is sent directly to the attacker's server.

### Finding Description
This is the closest structural analog to the Sherlock report: the Sherlock bug is "a security-relevant registration/validation step (`enterMarkets`) is skipped, so a downstream trust check (`borrowAllowed`) makes a wrong decision based on incomplete state." Here, the analogous missing invariant is: **the host-classification step (`getEndpointKind`) treats an untrusted signal (`WWW-Authenticate` header content, fully controlled by the remote server) as proof of GitHub identity, and no subsequent step re-validates the host before it is used as the destination for OAuth secrets.** Existing guards elsewhere in the codebase (`isClonePathSensitive`, `resolveWithin`, `sanitizeCloneName`, OAuth CSRF `state` check in `resolveOAuthRequest`) do not apply here because none of them constrain *which host* the sign-in/OAuth flow targets — that decision is made purely from the header heuristic in `getEndpointKind`.

The attacker's entry point requires only a remote/proxy the victim's Desktop instance talks to (e.g., a malicious `git clone`/`fetch` URL, a compromised/MITM'd Enterprise mirror, or a corporate proxy under attacker control) — no local access, no leaked credentials, and no unnatural user steps beyond the routine "sign in" dialog a user would normally trust during a git operation.

### Impact Explanation
Successful exploitation exfiltrates the GitHub Desktop application's OAuth `client_secret` (and elicits the user to authorize an OAuth code exchange against the attacker's server, letting the attacker capture the resulting authorization `code` too). This is a genuine "unauthorized OAuth" / credential-exfiltration primitive per the task's valid-impact criteria, since it silently redirects an app secret and a user-initiated OAuth grant to a host the user never intended to trust.

### Likelihood Explanation
Moderate-to-high: any attacker who can respond to a `git fetch`/`clone`/`push`/submodule-update HTTP(S) request (own the remote, or sit as a network proxy/MITM on an HTTP fetch) can add the single response header `WWW-Authenticate: realm="GitHub"` to trigger the misclassification, no other precondition is needed. The user must click through the resulting sign-in prompt, but the prompt is presented as a normal, expected part of the git workflow ("Sign in to GitHub Enterprise"), which is a realistic condition, not social engineering beyond what the app itself asks for.

### Recommendation
Do not trust the `WWW-Authenticate` realm header alone to classify a host as GitHub Enterprise. At minimum, corroborate with an authenticated check (e.g., `isGitHubHost()` against the actual endpoint) before offering the Enterprise sign-in flow, and/or restrict `promptForGitHubSignIn`/`setSignInEndpoint` to hosts the user has explicitly configured as trusted GHE endpoints (e.g., ones added in Preferences), rather than any host encountered incidentally during a git network operation.

### Proof of Concept
1. Host a git-served HTTP(S) endpoint (or MITM/proxy an existing one) that requires auth and, on a 401 response for a `git-upload-pack`/`git-receive-pack` request, includes header: `WWW-Authenticate: Basic realm="GitHub"`.
2. Get the victim to perform any git network operation against this endpoint in Desktop (clone, fetch, add as a submodule remote, etc.) — none of this requires the endpoint to look like GitHub in the URL.
3. Desktop's credential-helper trampoline calls `getEndpointKind()`, which pattern-matches the realm string and returns `'enterprise'`. [1](#0-0) 
4. Since no matching account exists for this endpoint, `getCredential()` calls `ui.promptForGitHubSignIn(attackerEndpoint)`. [2](#0-1) 
5. This opens a "Sign in" dialog and calls `dispatcher.setSignInEndpoint(attackerOrigin)`, entering the Authentication step for the attacker's host. [6](#0-5) 
6. When the user completes the browser OAuth step, `requestOAuthToken(attackerEndpoint, code)` sends `client_id`, `client_secret`, and `code` via `POST` to the attacker's `login/oauth/access_token` path. [5](#0-4)

### Citations

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

**File:** app/src/lib/stores/sign-in-store.ts (L394-459)
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
