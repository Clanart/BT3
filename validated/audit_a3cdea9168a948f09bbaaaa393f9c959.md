### Title
GitHub Desktop's OAuth `client_secret` and full Enterprise sign-in flow can be triggered against an attacker-controlled remote via a spoofed `WWW-Authenticate` header - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`Ignite::releaseLockedTokens` trusted a boolean (`failed`) too late for `QI`-staked registrations, so the "reward eligibility" state (`qiRewardEligibilityByNodeId`) was never gated off before `ValidatorRewarder.claimRewards` paid out — a classification/eligibility check that should have been authoritative was skipped, letting an attacker-controlled failure condition still earn a privileged payout. The Desktop analog is `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, which classifies a git remote as a trusted GitHub/Enterprise host based on an unauthenticated, attacker-suppliable `WWW-Authenticate` header instead of requiring the same independent verification (`isGitHubHost`) used in the fallback path. That misclassification is authoritative enough to launch Desktop's full OAuth Enterprise sign-in flow — including sending the app's OAuth `client_secret` — against a host the attacker fully controls.

### Finding Description
When Git needs credentials for a remote, it invokes Desktop's credential helper (`createCredentialHelperTrampolineHandler` → `getCredential`) and forwards any `WWW-Authenticate` response headers received from the remote server as `wwwauth[]` entries in the credential request.

`getEndpointKind` uses these attacker-controlled header values as a "happy path" shortcut to decide whether a URL is a GitHub host, before ever performing the legitimate network-based verification (`isGitHubHost`, which independently probes `${endpoint}/meta` and checks for the `x-github-request-id` response header): [1](#0-0) 

Any HTTP(S) git remote (or a MITM proxy sitting on the fetch/clone/push path) can respond to Git's authentication challenge with `WWW-Authenticate: Basic realm="GitHub"` and this single unauthenticated string is enough to make Desktop treat that remote as `'enterprise'` — completely bypassing the `isGitHubHost` meta-endpoint check meant to be the real trust gate.

Once classified as `'enterprise'` with no existing matching account, `getCredential` immediately triggers a real GitHub Enterprise sign-in flow: [2](#0-1) 

`promptForGitHubSignIn(endpoint)` starts that flow scoped to the attacker's own URL: [3](#0-2) 

`SignInStore.authenticateWithBrowser` then opens the system browser to the attacker's own domain with a CSRF `state` token embedded in the query string, and stores that state for later validation: [4](#0-3) 

Because the attacker's server rendered the authorize page and therefore already knows the `state` value (it was sent to them in the query string), they can trivially construct a matching `x-github-client://oauth?code=...&state=...` deep link redirect. Desktop parses this via `parseAppURL`/`dispatchURLAction`, and `resolveOAuthRequest` validates only that the state matches what Desktop itself generated — which it does, since the attacker copied it verbatim: [5](#0-4) 

`requestOAuthToken` then POSTs Desktop's confidential OAuth `client_id`/`client_secret` directly to the attacker's endpoint to exchange the code: [6](#0-5) 

The corrupted value is the trust classification produced by `getEndpointKind` — it should only ever be `'enterprise'`/`'github.com'` when independently verified (as `isGitHubHost` does), but the `wwwauth[]` shortcut lets the remote server assert that classification about itself with a single response header, with no cryptographic or out-of-band verification.

### Impact Explanation
A malicious or compromised git remote/proxy can cause Desktop to leak its embedded OAuth `client_secret` to itself and to complete an attacker-observable OAuth code exchange, and can force the user into a "Sign in to GitHub Enterprise" UI flow pointed at a domain the attacker fully controls. This is credential/token exfiltration triggered purely by an attacker-controlled git remote/proxy response, without any local access, admin rights, or prior compromise of the host — squarely in the accepted impact category (attacker-controlled remote/proxy response leading to credential/token exfiltration and unauthorized account-binding-style flows).

### Likelihood Explanation
The trigger requires only that the user fetch/clone/push from (or be MITM'd on the path to) an HTTP(S) remote that returns a 401 with a crafted `WWW-Authenticate: ... realm="GitHub"` header — an entirely passive, off-repository-content action that is part of normal Git protocol negotiation and requires no unusual user interaction beyond adding/using a remote, which is standard Desktop usage.

### Recommendation
Do not treat the `wwwauth[]` header content as authoritative for host classification. Always run the independent `isGitHubHost` verification (or an equivalent authenticated check) before treating a non-`.github.com`/`.ghe.com` URL as an "enterprise" GitHub host, and require that verification to succeed before invoking `promptForGitHubSignIn`. Additionally, bind the OAuth `state` to the specific `endpoint` being signed into and validate that the `code`-exchange target endpoint was one Desktop itself resolved via a verified host, not one supplied solely via an unauthenticated response header.

### Proof of Concept
1. Stand up an HTTP(S) git server (e.g., using `http-backend` or any proxy) that, on any authentication challenge for a `git-upload-pack`/`git-receive-pack` request, responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone/add this remote and trigger a fetch (or push) so Git invokes the credential helper.
3. Observe `getEndpointKind` return `'enterprise'` for this host (per `app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`) without any `isGitHubHost` network probe.
4. Observe Desktop launch `promptForGitHubSignIn(endpoint)` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-104`) and open the system browser to `http://attacker-host/login/oauth/authorize?client_id=...&state=<csrf>`.
5. From the attacker's server, read `state` from the incoming request and redirect the browser to `x-github-client://oauth?code=<attacker-code>&state=<csrf>`.
6. Confirm Desktop's `resolveOAuthRequest` accepts the state and `requestOAuthToken` (`app/src/lib/api.ts:2370-2395`) POSTs `client_id`/`client_secret`/`code` to `http://attacker-host/login/oauth/access_token`, which the attacker's server logs.

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
