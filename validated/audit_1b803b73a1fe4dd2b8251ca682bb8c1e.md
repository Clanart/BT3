## Analysis Summary

The CPI report's broken invariant is: *"a value can be overwritten by a less-trusted actor with no additional check when it should require elevated trust because subsequent logic (redemption/withdrawal) fully trusts the stored value."*

The closest Desktop analog I could substantiate with local code is in the git-credential trampoline's endpoint-kind classification, which fully trusts a server-supplied `WWW-Authenticate` header to decide whether a host should be treated as "enterprise" (i.e., a GitHub Enterprise instance worthy of the OAuth sign-in flow), with no verification against the host itself. [1](#0-0) 

That classification result then unconditionally drives the credential helper into prompting a GitHub/Enterprise sign-in for whatever `endpoint` string was derived from the connection, without any check that this host was previously known/trusted: [2](#0-1) 

`promptForGitHubSignIn` takes that attacker-influenced endpoint at face value and starts the Enterprise sign-in flow against it: [3](#0-2) 

`setEndpoint`/`authenticateWithBrowser` then open the browser to `${endpoint}/login/oauth/authorize?client_id=...` and, on completion, `requestOAuthToken` will `POST` the app's `client_id`/`client_secret` to `${endpoint}/login/oauth/access_token`: [4](#0-3) [5](#0-4) 

### Title
Server-Controlled `WWW-Authenticate` Header Misclassifies Arbitrary Host as "Enterprise", Triggering OAuth Client-Secret Exfiltration - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind` decides whether a host being contacted by git (during clone/fetch/push) should be treated as a GitHub Enterprise host purely based on a `realm="GitHub"` string in a `WWW-Authenticate` response header that the remote server itself supplies — with no cross-check that the host is actually a GitHub-compatible API. [6](#0-5)  This is directly analogous to the CPI bug: a value (`endpointKind`) that gates a privileged action (initiating an OAuth sign-in / account-binding flow, and, if no account matches, sending secrets to that host) is set from untrusted input without a stronger validation step.

### Finding Description
When git performs an HTTPS operation against a remote controlled or MITM'd by an attacker, that server can return a `401` response with `WWW-Authenticate: Basic realm="GitHub"`. Desktop's credential helper (invoked by git as `credential.helper=desktop` for every fetch/push, see `GIT_CONFIG_PARAMETERS` in [7](#0-6) ) reads this header and classifies the host as `'enterprise'` instead of `'generic'`. [8](#0-7) 

`getCredential` then checks whether any existing account matches that host's derived API endpoint; if none does, it calls `promptForGitHubSignIn(endpoint)` with the attacker-chosen `endpoint`. [9](#0-8)  `promptForGitHubSignIn` starts the Enterprise sign-in flow scoped to that arbitrary origin. [10](#0-9)  The Enterprise sign-in flow's `setEndpoint`/`authenticateWithBrowser` open the system browser to `<endpoint>/login/oauth/authorize` and, once the OAuth code is returned, `requestOAuthToken` performs a server-side `POST` of the client secret to `<endpoint>/login/oauth/access_token`. [11](#0-10) 

Existing guards do not stop this: `getEndpointKind` never validates the header value against the actual host being contacted, `validateURL` in the Enterprise sign-in flow only checks URL syntax/HTTPS, not identity, and `setEndpoint` only special-cases `github.com`/`api.github.com`, not arbitrary attacker hosts. [12](#0-11) 

### Impact Explanation
An attacker who controls a cloned/fetched repository's remote (or intercepts/serves proxy responses for it) can cause GitHub Desktop to: (1) prompt the victim to "sign in to GitHub Enterprise" against the attacker's own domain while it is displayed as if it were a legitimate credential prompt, and (2) if the victim completes the OAuth flow, exfiltrate the app's OAuth `client_secret` to the attacker-controlled endpoint, and/or bind an attacker-influenced account/token into the local `AccountsStore` (`addAccount` unconditionally overwrites any existing entry for that endpoint — [13](#0-12) ). This matches the "unauthorized OAuth or account binding" / "credential exfiltration" impact classes.

### Likelihood Explanation
Requires the victim to interact with an attacker-controlled remote (clone/fetch from it, or a MITM'd/compromised legitimate remote) and to click through a sign-in prompt that resembles the normal Enterprise flow; this is within the "attacker controls a git remote/proxy response" threat model explicitly allowed by the task's valid-impact criteria, though it still needs an unsuspecting user to complete the sign-in step, which lowers likelihood somewhat.

### Recommendation
Do not derive trust classification (`'enterprise'` vs `'generic'`) solely from a server-supplied header. Require an independent signal (e.g., an actual successful call to a well-known GitHub API path, or a match against a user-configured/allow-listed enterprise host list) before offering the GitHub/Enterprise sign-in flow, and always display the literal host being authenticated against prominently in the sign-in dialog so users can detect spoofed enterprise prompts.

### Proof of Concept
1. Attacker sets up `evil.example.com` serving git-over-HTTPS and, on unauthenticated requests, returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds `https://evil.example.com/foo/bar.git` as a remote (or is redirected there via a compromised proxy/mirror) and performs `git fetch`/`git push` in Desktop.
3. Desktop's trampoline `getEndpointKind` sees the header and returns `'enterprise'`. [8](#0-7) 
4. No account matches `evil.example.com`, so `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com')`. [14](#0-13) 
5. Victim completes the shown "sign in to Enterprise" flow; the app POSTs `client_id`/`client_secret` to `https://evil.example.com/login/oauth/access_token`. [15](#0-14) 

**Note on completeness:** I could not fully trace, within the remaining tool budget, the exact deep-link/OAuth-callback handler that supplies the `code` to `requestOAuthToken` (likely in `parse-app-url.ts` / dispatcher OAuth handling) to confirm the callback URL scheme cannot be spoofed independently; this final leg of the chain should be verified in a full session before treating the client-secret-exfiltration impact as fully proven end-to-end.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-165)
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

**File:** app/src/lib/stores/sign-in-store.ts (L411-437)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L143-143)
```typescript
        GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'credential.helper=' 'credential.helper=desktop'`,
```

**File:** app/src/lib/stores/accounts-store.ts (L116-126)
```typescript
    const accountsByEndpoint = this.accounts.reduce(
      (map, x) => map.set(x.endpoint, x),
      new Map<string, Account>()
    )
    accountsByEndpoint.set(account.endpoint, account)

    this.accounts = sortAccounts([...accountsByEndpoint.values()])

    this.save()
    return account
  }
```
