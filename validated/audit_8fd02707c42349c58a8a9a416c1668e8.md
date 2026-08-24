### Title
Attacker-controlled `WWW-Authenticate` realm from a git remote/proxy can silently redirect GitHub Desktop's Enterprise OAuth sign-in flow to an attacker-controlled endpoint, leaking the app's OAuth `client_secret` and hijacking account binding - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The upstream Tessera bug is about an unguarded state transition (`approve()` without resetting to zero first) that a single "trusted-looking" input (the token contract) can permanently break. The GitHub Desktop analog is a similarly unguarded trust decision: Desktop's git credential-helper trampoline classifies a remote endpoint as a genuine GitHub/Enterprise host based solely on an **unauthenticated `WWW-Authenticate` header value** returned by the git server/proxy, and then feeds that attacker-supplied endpoint directly into the real GitHub sign-in/OAuth flow.

### Finding Description
When git needs credentials for an HTTPS remote, it forwards any `WWW-Authenticate` response headers to Desktop's credential helper as `wwwauth[]` fields. `getEndpointKind` trusts this content to decide whether the host is a GitHub Enterprise instance: [1](#0-0) 

If the realm string contains `GitHub`, the endpoint is classified `'enterprise'` even though nothing about the TLS certificate, hostname, or Desktop's own known-endpoint list was checked - this value comes straight from the remote server (or a MITM proxy sitting on the git connection), which is exactly the "attacker controls ... a git remote/proxy response" primitive named in the task.

If Desktop has no existing account for that "enterprise" endpoint, it goes straight to the real sign-in UI instead of silently failing: [2](#0-1) 

`promptForGitHubSignIn` then wires the attacker-controlled `endpoint` directly into the production Enterprise sign-in flow, bypassing the interactive endpoint-entry screen a user normally goes through (which is the only place URL validation happens): [3](#0-2) 

From there, the standard OAuth flow runs against the attacker's `origin`: Desktop opens the OS browser to that origin's `/login/oauth/authorize` with Desktop's real `client_id`: [4](#0-3) 

and, once a code is returned, exchanges it by POSTing Desktop's `client_id`/`client_secret` to that same attacker-controlled origin: [5](#0-4) 

The broken invariant mirrors the Tessera bug precisely: a value that should only ever be set from a validated/trusted source (the seaport `conduit`, or here the sign-in `endpoint`) is instead derived from untrusted attacker-controlled input and then used unconditionally in a security-sensitive operation (approval, or here credential/OAuth submission), with no existing guard rejecting the mismatch.

### Impact Explanation
This matches the "unauthorized OAuth or account binding" and "credential/token exfiltration" categories explicitly listed as valid: a malicious or compromised git host (which the victim only needs to `git fetch`/`git clone`/`git pull` from - no local access, no admin rights, no pre-existing malware) can:
- Cause Desktop's real GitHub sign-in popup and browser-based OAuth flow to target an attacker's domain while the user believes they are authenticating "GitHub Enterprise" access for the repository they just added.
- Leak the OAuth `client_secret` embedded in Desktop's `api.ts` module to the attacker's server via `requestOAuthToken`.
- Potentially bind/replace an account association in `AccountsStore` (`app/src/lib/stores/accounts-store.ts`) with attacker-influenced data returned from the fake token exchange.

### Likelihood Explanation
The trigger only requires the victim to add or already have a remote pointing at a server the attacker controls (or a MITM/compromised proxy in the path) and for Desktop to attempt an HTTPS git operation against it that fails auth with a crafted `WWW-Authenticate: Basic realm="GitHub"` header - a single unauthenticated HTTP response header, fully within reach of anyone who can host or intercept a git remote referenced by the victim's repository.

### Recommendation
Do not classify a remote as a first-party GitHub/Enterprise endpoint based on an unauthenticated `WWW-Authenticate` realm string alone. Restrict the `'enterprise'` classification path (and therefore the OAuth-based sign-in prompt) to endpoints that are already registered/known to Desktop (present in `accounts-store.ts`) or independently verified (e.g., via the GitHub Enterprise `/meta` endpoint over a validated TLS connection), matching the same `validateURL` checks used in the manual `SignInStep.EndpointEntry` flow in `app/src/lib/stores/sign-in-store.ts`, before ever invoking `promptForGitHubSignIn`.

### Proof of Concept
1. Host a git-over-HTTPS server (or a MITM proxy) that responds to an authenticated fetch request with `WWW-Authenticate: Basic realm="GitHub"`.
2. Have the victim add this server as a remote and run `git fetch`/`git pull` in GitHub Desktop.
3. Git forwards the header to Desktop's credential helper as `wwwauth[0]=Basic realm="GitHub"`; `getEndpointKind` (`trampoline-credential-helper.ts:157-163`) classifies the host as `'enterprise'`.
4. Since no account exists for that host, `getCredential` (`trampoline-credential-helper.ts:109-124`) calls `ui.promptForGitHubSignIn(endpoint)` with the attacker's URL.
5. `TrampolineUIHelper.promptForGitHubSignIn` (`trampoline-ui-helper.ts:80-99`) starts `beginEnterpriseSignIn`/`setSignInEndpoint(origin)` against the attacker's origin and shows the legitimate-looking GitHub Desktop sign-in popup.
6. The user clicks "Sign in," Desktop opens the browser to `attacker-origin/login/oauth/authorize?client_id=...` (`sign-in-store.ts:284-303`), and upon completion of the (attacker-controlled) OAuth dance, `requestOAuthToken` (`api.ts:2370-2390`) POSTs Desktop's `client_id`/`client_secret` to the attacker's server.

Note: I was not able to fully inspect the exact implementation of `Dispatcher.setSignInEndpoint`/`AppStore._beginEnterpriseSignIn` (only `sign-in-store.ts`'s public `setEndpoint`, which performs `validateURL`, was located) - the finding assumes, based on the direct call pattern in `trampoline-ui-helper.ts:91-92`, that this path does not go through the same interactive `validateURL` gate used by the manual Enterprise sign-in UI. Confirming that exact bypass would require reading `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/stores/app-store.ts` in full, which the index did not surface for this query.

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

**File:** app/src/lib/api.ts (L2370-2390)
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
```
