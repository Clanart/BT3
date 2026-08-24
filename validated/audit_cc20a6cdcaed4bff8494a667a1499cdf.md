## Title
Malicious remote can trick the credential‑helper into launching a real GitHub Enterprise sign‑in flow against an attacker‑controlled host, exfiltrating the user's OAuth code/token - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The bug‑class in the Sherlock report is: a downstream call site sets a "safety" parameter that the caller relies on to route a value back to the rightful owner, but that assumption silently breaks and the value is instead handed to the wrong party. The closest verified analog in this repository is in the Git credential‑helper trampoline: `getEndpointKind` classifies a remote endpoint as `'enterprise'` based solely on a `WWW-Authenticate` header value supplied by the remote server itself, and that classification is then used to decide whether to launch Desktop's real "Sign in to GitHub Enterprise" OAuth flow against that same, attacker‑controlled endpoint.

### Finding Description
When Git performs any HTTP(S) operation (`fetch`, `push`, `clone`) against a remote it doesn't have cached credentials for, it invokes Desktop's credential helper trampoline with the `wwwauth[]` header(s) the server returned. `getEndpointKind` trusts these attacker‑supplied header values without any verification against the real GitHub API: [1](#0-0) 

If the realm string contains `GitHub`, the endpoint is classified `'enterprise'`, i.e. treated as a legitimate GitHub Enterprise instance, purely because the remote said so — there is no TLS pinning, no call to the real GitHub API, and no cross-check with `isGitHubHost` in this branch (that check is only reached later, as a fallback, and even it just probes `/meta` on the same attacker-controlled host).

That classification then flows into `getCredential`: [2](#0-1) 

Since `endpointKind !== 'generic'` and the attacker's host won't match any existing account's `apiEndpoint`, Desktop calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` being the attacker-controlled URL taken verbatim from the credential request: [3](#0-2) 

This starts a *real* Desktop "Enterprise sign‑in" flow (`beginEnterpriseSignIn` + `setSignInEndpoint`) whose OAuth authorize/token URLs are built directly from that attacker-supplied origin: [4](#0-3) 

`_resolveOAuthRequest`/`resolveOAuthRequest` later exchanges the authorization `code` for an access token against `endpoint` (attacker's host), and on success calls `fetchUser(endpoint, token)`: [5](#0-4) 

Nothing in this path re-verifies that `endpoint` is a real, previously-vetted Enterprise/GitHub host before routing the OAuth code/token exchange to it — the only "guard" (`isGitHubHost`) is skipped whenever the server itself claims a `realm="GitHub"` in its `WWW-Authenticate` header, which is exactly the value the attacker controls.

The parallel to the Sherlock bug: just as `depositUnderlyingToken(..., false)` blindly trusted a hard‑coded assumption ("there should never be excess ETH here") and thereby routed value to the wrong recipient, `getEndpointKind` blindly trusts an externally supplied signal (`wwwauth[realm="GitHub"]`) to decide it's safe to treat an arbitrary host as a genuine GitHub endpoint, thereby routing the user's real sign‑in flow (and ultimately their OAuth code/token) to that host.

### Impact Explanation
An attacker who controls a git remote (e.g. a repository the victim clones, or a fork/submodule/LFS URL embedded in a cloned repo, or a man‑in‑the�middle/rogue proxy responding to a fetch) can trigger a native-looking "Sign in to GitHub Enterprise" dialog inside GitHub Desktop that is actually wired to authenticate against the attacker's server. Because the prompt is rendered by Desktop's own trusted UI (`PopupType.SignIn`), and the endpoint field is prefilled/managed internally, a victim has no easy way to notice the request is going to a foreign host rather than their real GHE instance. This can lead to leakage of the OAuth `code`, and depending on how the attacker's fake OAuth endpoint responds, capture of the resulting access token intended for the user's account — i.e., unauthorized OAuth/account-binding and token exfiltration, matching the "unauthorized OAuth or account binding" / "credential/token exfiltration" categories in the report's scope.

### Likelihood Explanation
The trigger requires only that the victim perform an ordinary Git network operation (fetch/push/clone) against a URL the attacker controls — e.g. a legitimate-looking cloned repository with a malicious submodule or LFS remote, which is squarely within the "attacker controls a cloned/fetched repository ... or a git remote/proxy response" scope of this task. No local access, admin rights, or prior compromise is required; the victim only needs to attempt to authenticate once against the hostile remote and click through the resulting native sign-in prompt, which looks identical to a legitimate first-time GHE sign-in.

### Recommendation
Do not classify an endpoint as `'enterprise'` (and thus eligible for a real OAuth sign-in flow) based solely on a self-reported `WWW-Authenticate` realm string. At minimum:
- Require a positive `isGitHubHost` verification (an actual network probe/certificate check against the candidate host) before offering to start any sign-in flow, rather than treating the header hint as sufficient on its own.
- Surface the destination host prominently and unambiguously in the sign-in popup so users can distinguish "sign in to your known GHE endpoint" from "sign in to a brand-new, remote-supplied endpoint."
- Consider requiring explicit user confirmation (out-of-band, e.g. via Preferences) before ever initiating an OAuth flow to a host that was not already an enrolled/known Enterprise endpoint.

### Proof of Concept
1. Attacker hosts a git-over-HTTPS server (or a proxy/MITM in front of one) that responds to Git's authentication probe with `WWW-Authenticate: Basic realm="GitHub"`.
2. Attacker distributes a repository whose `.gitmodules` (or `.lfsconfig`, or a remote the victim is asked to add) points to this server, e.g. `https://update-notional.example/attacker.git`.
3. Victim clones/fetches this repository in GitHub Desktop. Git invokes the credential helper trampoline; `command.stdin` includes `wwwauth[0]=Basic realm="GitHub"` and `url=https://update-notional.example/...`.
4. `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:153-165`) returns `'enterprise'` purely from that header.
5. `getCredential` finds no matching stored account for this endpoint and calls `ui.promptForGitHubSignIn('https://update-notional.example')`.
6. Desktop shows its normal "Sign in to GitHub Enterprise" popup and, on submission, drives the OAuth authorize/token exchange against `update-notional.example` (`getOAuthAuthorizationURL`/`requestOAuthToken` in `app/src/lib/api.ts:2357-2395`, consumed in `app/src/lib/stores/sign-in-store.ts:332-359`), sending the victim's authorization code (and subsequently the token flow) to the attacker's server instead of a legitimate GitHub endpoint.

Note: I was not able to fully trace how `beginEnterpriseSignIn`/`setSignInEndpoint` validates reachability before allowing the OAuth authorize URL to be opened (i.e., whether `validateURL` in `sign-in-store.ts` is invoked on this credential-helper-driven path or only on the manual "Enterprise sign-in" UI path); this would need to be confirmed by tracing `dispatcher.beginEnterpriseSignIn`/`setSignInEndpoint` end-to-end, which the current index did not fully surface.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
```typescript
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
