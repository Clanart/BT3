### Title
Malicious Git remote can spoof GitHub-Enterprise identity via `WWW-Authenticate` header to trigger OAuth/PAT sign-in against an attacker-controlled endpoint - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The bug-class in the external report is a broken invariant: a value that should only be set/trusted after a verified precondition can instead be set/spoofed by an attacker-controlled external response, causing the application to act on unverified attacker-controlled state. In `BeefyZapRouter`, the invariant was "allowance must be attacker-uncontrolled before re-approving." In GitHub Desktop, the analogous invariant is "an HTTPS remote must be verified as a genuine GitHub/GHE host before Desktop treats it as one and initiates a GitHub sign-in/OAuth flow bound to that host's origin." That invariant is broken in `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts`.

### Finding Description
When Git performs an HTTPS authentication attempt, Desktop's credential-helper trampoline classifies the remote endpoint to decide how to source credentials: [1](#0-0) 

Specifically, Desktop trusts an unauthenticated `WWW-Authenticate` header echoed back by the remote git server itself:
```
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
This header comes directly from the git server responding to the clone/fetch/push request — i.e., it is fully attacker-controlled if the remote is malicious or compromised (per the Valid Impact scope: "a git remote/proxy response"). If the attacker's server merely returns `WWW-Authenticate: Basic realm="GitHub"` on a 401, `getEndpointKind` short-circuits and returns `'enterprise'` **without** performing the legitimate verification path that exists later in the same function — the `isGitHubHost()` check, which makes an actual `HEAD /meta` request and validates the `x-github-request-id` response header: [2](#0-1) 

Because `endpointKind !== 'generic'` and no existing account matches the spoofed endpoint, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` then binds the *attacker's own origin* as the sign-in endpoint and launches the Enterprise sign-in dialog against it: [4](#0-3) 

This flows into `SignInStore.setEndpoint`/`authenticateWithBrowser`, which builds an OAuth authorize URL rooted at the attacker's endpoint and opens it in the browser, and (for PAT/basic auth) will exchange whatever the user types for a token/user record fetched from that same attacker endpoint: [5](#0-4) [6](#0-5) 

The existing guard that is meant to stop exactly this — `isGitHubHost`'s network-verified `x-github-request-id` check — is never reached, because the `wwwauth[]` branch returns early with no server-side verification at all.

### Impact Explanation
A malicious or compromised HTTPS git remote (something the user is instructed to clone/fetch/push, satisfying the "attacker controls ... a git remote/proxy response" criterion) can:
- Force GitHub Desktop to display a "Sign in to GitHub Enterprise" dialog whose displayed/underlying endpoint is fully attacker-chosen (`credentialHelperUrl`), impersonating a legitimate GHE instance.
- Cause Desktop to open an OAuth `login/oauth/authorize` URL rooted at the attacker's server, or to submit PAT/basic credentials entered by the user directly to the attacker's server via `requestOAuthToken`/`fetchUser`.
- Result in unauthorized account binding: if the flow completes, an `Account` object bound to the attacker's endpoint (and whatever token the attacker's fake OAuth/API endpoint returns) is added to Desktop's `AccountsStore`, potentially confusing subsequent credential lookups for that host, or exfiltrate the user's real credentials/token if they mistake the dialog for a legitimate prompt and paste an existing PAT.

This satisfies the "unauthorized OAuth or account binding" and "credential/token exfiltration" impact categories, not merely DoS.

### Likelihood Explanation
No local access, admin rights, or prior compromise is required — only that the user adds/clones/interacts with a repository whose remote is attacker-controlled or has been compromised (a normal, in-scope Desktop workflow). The attacker only needs to return a specific `WWW-Authenticate` header on a 401 response, which is trivial for any HTTP server the attacker operates. The generic-git-auth prompt (`promptForGenericGitAuthentication`) exists precisely for non-GitHub hosts and would be the "safe" path if `getEndpointKind` correctly returned `'generic'`; the vulnerability specifically defeats that safe classification.

### Recommendation
Do not trust the `wwwauth[]`/`realm="GitHub"` header as sufficient evidence of a GitHub/GHE host. Treat it only as a hint to prioritize/trigger the existing verified check (`isGitHubHost`, the `x-github-request-id`-validated request) rather than as a substitute for it, and fail closed to the `'generic'` credential path (or require explicit user confirmation naming the real hostname) whenever verification cannot be completed.

### Proof of Concept
1. Stand up an HTTPS git server (e.g., a simple `git-http-backend` wrapper or reverse proxy) at `https://evil.example`.
2. Configure it so that on any unauthenticated request it returns HTTP 401 with header `WWW-Authenticate: Basic realm="GitHub"`.
3. In GitHub Desktop, clone (or add as a remote and fetch/push) `https://evil.example/some/repo.git` while not already signed in to that host.
4. Git forwards the `wwwauth[]=Basic realm="GitHub"` field to Desktop's credential-helper trampoline (`command.stdin`), which is parsed in `createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind`.
5. Observe that `getEndpointKind` returns `'enterprise'` (bypassing `isGitHubHost`'s verified `/meta` check), and Desktop shows the GitHub Enterprise sign-in dialog with `credentialHelperUrl` set to `https://evil.example`, initiating OAuth/PAT exchange rooted at the attacker's server.

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

**File:** app/src/lib/api.ts (L2465-2491)
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
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
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

**File:** app/src/lib/stores/sign-in-store.ts (L284-330)
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
