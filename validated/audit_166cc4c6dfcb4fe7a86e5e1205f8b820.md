### Title
Spoofed `WWW-Authenticate` realm from a malicious git remote triggers an unverified Enterprise OAuth sign-in against attacker's own host, allowing rogue account binding - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` in the credential-helper trampoline classifies an unknown git remote as GitHub-related ("enterprise") **solely based on a `WWW-Authenticate` header value that the remote server itself supplies**, without any independent verification. A malicious git server (a repository remote the victim clones/fetches from, or a proxy sitting in front of one) can set `WWW-Authenticate: Basic realm="GitHub"` on its 401 response and be treated by Desktop exactly like a real GitHub Enterprise host, causing Desktop to launch its OAuth "sign in with browser" flow with that attacker-controlled host as the `endpoint`.

### Finding Description
`getEndpointKind` trusts a header echoed back by the remote itself: [1](#0-0) 

This is invoked from `getCredential()` on every `git credential get` for a host that isn't already `github.com`/`ghe.com` and has no existing matching account: [2](#0-1) 

Once misclassified as `enterprise`, and since no account exists yet for that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

Because `hostname !== 'github.com'`, this calls `dispatcher.beginEnterpriseSignIn(cb)` then `dispatcher.setSignInEndpoint(origin)` where `origin` is the **attacker's own host** — there is no verification that this host is an actual GHE instance before advancing to the Authentication step (`setEndpoint` only checks URL syntax/protocol and reachability, not GHE identity): [4](#0-3) 

If the user proceeds with the resulting "Sign in using your browser" prompt, `authenticateWithBrowser()` opens `getOAuthAuthorizationURL(endpoint, csrfToken)` in the system browser — i.e. Desktop's real OAuth `client_id`, `scope`, and the freshly generated CSRF `state` token are sent directly to the attacker's server as query parameters of the navigation request: [5](#0-4) [6](#0-5) 

Because the attacker's own server received the `state` value directly (it wasn't kept secret from them — no interception needed), the attacker can then have the user open a deep link `x-github-client://oauth?code=<arbitrary>&state=<the harvested state>`. Desktop's `resolveOAuthRequest` only checks that `action.state === oauthState.state`, which will match: [7](#0-6) 

`requestOAuthToken` and the subsequent `fetchUser` call are then made against `getHTMLURL(endpoint)` — again the attacker's own host — meaning the attacker's server fully controls the `access_token` and the "authenticated user" JSON returned: [8](#0-7) 

The resulting `Account` is added to Desktop's account list via `emitAuthenticate`, under whatever identity/token the attacker chooses: [9](#0-8) 

**The corrupted value:** the `endpointKind` classification (and downstream `endpoint`/`origin` used for `beginEnterpriseSignIn`) is derived from attacker-controlled response data (`WWW-Authenticate` header) rather than a trustworthy, independently-verified signal that the host is actually a GitHub/GHE instance.

**Why existing guards don't stop it:**
- `findGitHubTrampolineAccount`'s strict origin match only protects *existing* accounts from being sent to the wrong host — it does nothing to stop a *new* sign-in flow from being started against an untrusted host.
- The OAuth CSRF `state` nonce normally defends against cross-flow/replay attacks, but here it provides no protection because the attacker's own server is the first party to receive it (via the authorize-URL query string), not a third party trying to guess/intercept it.
- `setSignInEndpoint`/`validateURL` only validate URL syntax, protocol, and reachability — never that the host is a genuine GitHub Enterprise Server.

### Impact Explanation
This satisfies "unauthorized OAuth or account binding": a git remote fully controlled by the attacker can get Desktop to complete what looks like a legitimate Enterprise sign-in but is bound to the attacker's server, adding an attacker-influenced `Account` (arbitrary login/avatar/token) into the user's Desktop account list. This corrupted account state can subsequently be picked up by other endpoint-matching logic (e.g., `getAccountForEndpoint`, `findAccountForRemoteURL`) for future operations against that same attacker-controlled hostname, and generally undermines the user's trust boundary between "real GitHub/GHE" and arbitrary remotes.

### Likelihood Explanation
Medium. The attacker only needs to control the HTTP response of a git remote/proxy the victim fetches from or clones (setting one response header), which matches the "attacker controls a git remote/proxy response" category. It does require the victim to proceed through the resulting sign-in prompt (click "Sign in using your browser"), which is a normal, expected interaction in this flow rather than an unnatural one, since the dialog explicitly states "Git requesting credentials to access `<credentialHelperUrl>`" without surfacing that the host is unverified.

### Recommendation
- Do not use the client-supplied `WWW-Authenticate` header alone to decide that a host is "enterprise"/GitHub-affiliated; require an independent server-side check (e.g., hitting a known GitHub/GHE metadata endpoint over HTTPS and validating a signed/trusted response) before treating an unknown host as GitHub-affiliated.
- Before advancing to `beginEnterpriseSignIn`/opening the OAuth authorize URL for a host discovered this way, surface a clear, unavoidable warning to the user that the host has not been verified as a real GitHub Enterprise instance and is being trusted solely based on a header the remote itself provided.
- Consider not auto-launching the full sign-in flow off the back of credential-helper heuristics for previously-unseen hosts at all, and instead always falling back to the generic-credential prompt unless the host has been positively confirmed as GitHub/GHE.

### Proof of Concept
1. Attacker sets up a git HTTP server at `https://attacker.example` and gets the victim to add it as a remote / clone from it in GitHub Desktop.
2. On an authenticated git request, the server responds `401` with `WWW-Authenticate: Basic realm="GitHub"`.
3. Desktop's credential helper (`getEndpointKind`) classifies `attacker.example` as `'enterprise'`; since no matching account exists, `getCredential` calls `ui.promptForGitHubSignIn('https://attacker.example')`.
4. Desktop shows the sign-in dialog ("Git requesting credentials to access attacker.example"); victim clicks "Sign in using your browser".
5. Desktop's browser opens `https://attacker.example/login/oauth/authorize?client_id=<real_id>&scope=...&state=<csrfToken>` — the attacker's server logs `csrfToken`.
6. Attacker's page instructs/redirects the victim to `x-github-client://oauth?code=anything&state=<csrfToken>`.
7. Desktop's `resolveOAuthRequest` accepts the state match and calls `requestOAuthToken('https://attacker.example', 'anything')`, POSTing to the attacker's own `/login/oauth/access_token`.
8. Attacker's server returns an arbitrary `access_token`; Desktop's `fetchUser` fetches "user" info from the attacker's server and adds the resulting `Account` to the victim's Desktop account list.

Note: I was not able to fully trace whether `isGitHubHost()` (used as a fallback network probe) performs additional certificate/identity validation that might mitigate part of this chain for hosts that skip the header-spoofing path — this was not fully explored due to tool/iteration limits, and a Devin session with full repo access could verify that function's implementation in `app/src/lib/api.ts` to confirm scope.

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

**File:** app/src/lib/stores/sign-in-store.ts (L284-303)
```typescript
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
```

**File:** app/src/lib/stores/sign-in-store.ts (L304-317)
```typescript
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

**File:** app/src/lib/api.ts (L2357-2368)
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
```

**File:** app/src/lib/api.ts (L2370-2396)
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
