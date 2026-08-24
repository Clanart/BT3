Good — this confirms the actual UI does display `credentialHelperUrl` to the user via `renderAuthenticationStep` (line 183-198 of `sign-in.tsx`), showing "Git requesting credentials to access {credentialHelperUrl}" with the actual attacker endpoint. So there is a visible domain, but it's shown in a small `<Ref>` tag inside a generic-looking "Sign in" dialog, not a security warning, and the user must actively notice the domain mismatch. This is a legitimate finding but with a UI mitigation reducing severity (user must fail to notice the shown URL).

### Title
Spoofed `WWW-Authenticate` Header from Malicious Git Remote Triggers Unintended GitHub Enterprise OAuth Flow and Leaks Desktop's OAuth Client Secret - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's Git credential-helper trampoline classifies an unknown remote host as a GitHub Enterprise ("enterprise") endpoint based solely on the content of the `WWW-Authenticate` HTTP header returned by that remote during Git's HTTPS auth negotiation, with no TLS/identity verification. An attacker-controlled Git server can therefore impersonate GitHub Enterprise purely by returning a crafted header, causing Desktop to auto-launch its real GitHub Enterprise sign-in/OAuth flow against the attacker's own domain, including sending Desktop's app-wide OAuth `client_id`/`client_secret` to that attacker-controlled host if the flow is completed.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes the configured credential helper (`credential.helper=desktop`, wired up in `withTrampolineEnv`) and forwards any `WWW-Authenticate` header fields it received from the server as `wwwauth[]` entries [1](#0-0) .

`getEndpointKind` in `trampoline-credential-helper.ts` decides whether the host is `github.com`, `ghe.com`, `enterprise`, or `generic`. For arbitrary hosts it trusts the header content emitted by the very server being contacted: if any `wwwauth[...]` value contains `realm="GitHub"`, it is classified as `'enterprise'` with no cryptographic or DNS-based verification of the claim: [2](#0-1) 

`getCredential` then checks whether any stored account matches this endpoint; since it won't for an attacker's domain, and the endpoint kind is not `'generic'`, it calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` in `trampoline-ui-helper.ts` then drives the real Enterprise sign-in machinery, using the attacker's own `origin` as the sign-in endpoint: [4](#0-3) 

`SignInStore.setEndpoint` accepts any syntactically valid HTTPS URL (only checked via `validateURL`, no allowlist or reachability-based GitHub verification of "real" GHE), and if the user proceeds with "Continue with browser", `authenticateWithBrowser` opens the OS browser at an OAuth authorize URL built from the attacker's endpoint: [5](#0-4) 

If the OAuth redirect completes (e.g., attacker's page silently completes the exchange or tricks the user), `resolveOAuthRequest` calls `requestOAuthToken(endpoint, code)`, which POSTs Desktop's OAuth `client_id` **and `client_secret`** to `{attacker_endpoint}/login/oauth/access_token`: [6](#0-5) [7](#0-6) 

The broken invariant is that the trust decision ("is this a GitHub Enterprise host, worthy of launching the real GHE OAuth flow") is made from data supplied by the untrusted party itself (the `WWW-Authenticate` header), rather than from any independently verified property of the endpoint. The only existing guard is a small `<Ref>{credentialHelperUrl}</Ref>` string shown inside the generic "Sign in" dialog [8](#0-7) , which does not stop the flow — it merely displays the attacker's URL in passing, with no explicit warning that this is an unexpected/unverified GitHub Enterprise host.

### Impact Explanation
Successful exploitation causes GitHub Desktop's own OAuth application secret (`ClientSecret`, shared across all Desktop installations) to be sent to an attacker-controlled server as part of the standard OAuth `access_token` exchange. It also triggers an unsolicited "Sign in to GitHub Enterprise" flow directed at an attacker-chosen domain merely because the user tried to fetch/pull/clone from (or push to) a malicious/compromised HTTP(S) remote — this can be used for phishing (a legitimate-looking Desktop sign-in dialog whose real target is attacker-controlled) and for OAuth-secret disclosure. This matches the requested impact classes of unauthorized OAuth flow triggering and credential/secret exfiltration.

### Likelihood Explanation
The only requirement is that the user perform a normal Git operation (`clone`, `fetch`, `pull`, `push`) against a remote controlled or proxied by the attacker (e.g., a malicious/compromised Git server, or a man-in-the-middle git proxy on an internal network) that responds with HTTP 401 and a spoofed `WWW-Authenticate: Basic realm="GitHub"` header. No prior credentials, admin rights, or unnatural user steps are needed to reach the sign-in prompt — it is triggered automatically by Desktop's credential-helper trampoline during ordinary auth negotiation. Completing the leak requires the user to click "Continue with browser," which is somewhat mitigated by the URL being shown in the dialog, but the dialog does not distinguish "trusted enterprise account" from "arbitrary attacker-chosen host masquerading as enterprise."

### Recommendation
Do not derive endpoint trust classification (`getEndpointKind`) from attacker-supplied `WWW-Authenticate` header content alone. At minimum, treat header-based "enterprise" detection as a weak heuristic that should never by itself trigger the full GHE OAuth sign-in flow with the app's shared `client_secret`; instead, require either an explicit user-initiated Enterprise sign-in (via Preferences) or independent verification (e.g., hitting a known GHE metadata endpoint) before offering to sign in. The sign-in dialog should also clearly flag that Git itself requested credentials for an unrecognized host, warning the user this was not something they initiated.

### Proof of Concept
1. Stand up an HTTPS Git server (`evil.example.com`) that, for any authenticated Git smart-HTTP request, returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone or add `https://evil.example.com/foo/bar.git` as a remote and perform a fetch/pull/push.
3. Git invokes the `desktop` credential helper with `wwwauth[0]=Basic realm="GitHub"`; `getEndpointKind` classifies `evil.example.com` as `'enterprise'` [2](#0-1) .
4. Desktop automatically shows a "Sign in" dialog whose flow is `beginEnterpriseSignIn` targeting `evil.example.com` [9](#0-8) .
5. If the user clicks "Continue with browser," Desktop opens `https://evil.example.com/login/oauth/authorize?client_id=...` in the system browser [10](#0-9) ; the attacker's server harvests the visit, and upon any callback with a code, Desktop's `requestOAuthToken` will POST the real Desktop `client_secret` to `evil.example.com/login/oauth/access_token` [11](#0-10) .

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L126-143)
```typescript
        GIT_ASKPASS: '',
        // This warrants some explanation. We're configuring the
        // credential helper using environment variables rather than
        // arguments (i.e. -c credential.helper=) because we want commands
        // invoked by filters (i.e. Git LFS) to be able to pick up our
        // configuration. Arguments passed to git commands are not passed
        // down to filters.
        //
        // We're using the undocumented GIT_CONFIG_PARAMETERS environment
        // variable over the documented GIT_CONFIG_{COUNT,KEY,VALUE} due
        // to an apparent bug either in a Windows Python runtime
        // dependency or in a Python project commonly used to manage hooks
        // which isn't able to handle the blank environment variables we
        // need when using GIT_CONFIG_*.
        //
        // See https://github.com/desktop/desktop/issues/18945
        // See https://github.com/git/git/blob/ed155187b429a/config.c#L664
        GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'credential.helper=' 'credential.helper=desktop'`,
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

**File:** app/src/ui/sign-in/sign-in.tsx (L183-198)
```typescript
  private renderAuthenticationStep(state: IAuthenticationState) {
    const credentialHelperInfo =
      this.props.isCredentialHelperSignIn && this.props.credentialHelperUrl ? (
        <p>
          Git requesting credentials to access{' '}
          <Ref>{this.props.credentialHelperUrl}</Ref>.
        </p>
      ) : undefined

    return (
      <DialogContent>
        {credentialHelperInfo}
        {browserSignInInfoContent}
      </DialogContent>
    )
  }
```
