### Title
Malicious git remote can spoof a "GitHub Enterprise" auth challenge to trigger a phishing-style OAuth sign-in flow - (File: app/src/lib/trampoline/trampoline-credential-helper.ts, app/src/lib/trampoline/trampoline-ui-helper.ts)

### Summary
When Git needs credentials for an HTTPS remote, the response's `WWW-Authenticate` header is forwarded into GitHub Desktop's credential-helper trampoline. `getEndpointKind` in `trampoline-credential-helper.ts` classifies any host that returns a `WWW-Authenticate` header containing `realm="GitHub"` as an `'enterprise'` endpoint, with no verification that the host is an actual GitHub Enterprise Server. This classification then drives `trampoline-ui-helper.ts`'s `promptForGitHubSignIn`, which starts a full "Sign in to your GitHub Enterprise instance" OAuth flow scoped to the attacker-controlled origin.

### Finding Description
`getEndpointKind` decides how to treat a credential request: [1](#0-0) 

The comment explicitly documents that Git forwards `WWW-Authenticate` headers "captured" from the remote and Desktop uses them as a "happy-path" to determine whether the host is a GitHub host, without making a request itself. Any HTTPS git server (attacker-controlled remote, or a MITM/malicious proxy on an HTTPS remote whose TLS is otherwise accepted) can return `WWW-Authenticate: Basic realm="GitHub"` on a 401 response and be classified as `'enterprise'`.

`getCredentialUrl` builds the endpoint solely from the credential fields Git passes through (`protocol`, `host`, `path`), which for an HTTPS remote is exactly the attacker-controlled remote's own hostname: [2](#0-1) 

When `getCredential` sees `endpointKind !== 'generic'` and no existing account matches that host, it calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` then unconditionally treats any non-`github.com` hostname as a GitHub Enterprise instance, begins the enterprise sign-in flow, and sets the sign-in endpoint to the attacker's own `origin`: [4](#0-3) 

The only gate before advancing to the `Authentication` step is `validateURL`, which merely checks that the string is a syntactically valid `https:` URL — it performs no verification that the host is a legitimate/known GitHub Enterprise server: [5](#0-4) 

Once in the `Authentication` step, the "Sign in using your browser" action opens a real OAuth authorization flow whose URL is derived from the attacker's endpoint (`getOAuthAuthorizationURL(endpoint)`/`requestOAuthToken(endpoint, ...)`/`fetchUser(endpoint, ...)` in `sign-in-store.ts`), all pointed at the attacker's server: [6](#0-5) 

The broken invariant is the same class as the report's seed bug: a security-relevant decision (here, "is this endpoint a trusted GitHub/Enterprise host?") is made from data that is fully controlled by the untrusted counterparty (the git remote's HTTP response), and no independent verification step exists to confirm the claim before granting a privileged action (initiating and completing a GitHub OAuth login and account binding).

### Impact Explanation
An attacker who controls a git remote (or can spoof HTTP responses to one, e.g. via a compromised/malicious host the victim adds as a remote) can cause Desktop to present the user with a legitimate-looking "Sign in to your GitHub Enterprise instance" dialog, then drive the browser-based OAuth flow to the attacker's own server. This can be used to phish the user (attacker's page can visually mimic a GHE login/consent page, or relay a real GitHub login form to harvest credentials/session), and any resulting OAuth code/token exchange (`requestOAuthToken`, `fetchUser`) is performed against the attacker's endpoint, letting the attacker mint a fabricated Account that Desktop then trusts and binds credentials to for future operations against that host (unauthorized OAuth/account binding). This matches the report's abstract impact class (bypassing an intended trust/fee-style check by using an alternate, less-checked path) applied to Desktop's authentication trust boundary.

### Likelihood Explanation
Triggering this requires only that the victim add/use a git remote pointing at an attacker-controlled or attacker-influenced HTTPS host and perform an ordinary `fetch`/`clone`/`push` that results in a 401 with a crafted `WWW-Authenticate` header — no local access, no malware, and no unusual user steps beyond the normal "enter credentials for this repo" flow that Desktop already prompts for. The classification logic is documented as intentionally trusting this header as a "happy path," so no existing guard (`validateURL` checks only URL syntax and protocol) stops an attacker from asserting `realm="GitHub"` for an arbitrary host.

### Recommendation
Do not treat the `WWW-Authenticate: realm="GitHub"` heuristic in `getEndpointKind` as sufficient to classify an endpoint as `'enterprise'` and thus a real GitHub product endpoint. Before initiating the GitHub Enterprise sign-in / OAuth flow from `promptForGitHubSignIn`, independently verify the host is actually a GitHub Enterprise Server (e.g., via the same kind of API probe used by `isGitHubHost`) rather than relying solely on the header captured from an untrusted server, and surface the raw destination host prominently in the sign-in UI so users can recognize suspicious values before authorizing.

### Proof of Concept
1. Attacker sets up an HTTPS git server at `https://evil.example.com/victim/repo.git` that, on any authenticated Git HTTP request, returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote in GitHub Desktop (e.g., cloning a link sent by the attacker) and performs a `fetch`.
3. Git invokes the Desktop credential-helper trampoline; `getEndpointKind` sees the `wwwauth[]` entry matching `realm="GitHub"` and returns `'enterprise'` [7](#0-6) .
4. Since no account exists for `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com/...')` [8](#0-7) .
5. `promptForGitHubSignIn` begins the enterprise sign-in flow and sets the endpoint to `https://evil.example.com`, passing `validateURL`'s https-only check [9](#0-8) .
6. The victim, believing this is a legitimate corporate GHE sign-in prompted by their own clone, clicks "Sign in using your browser," and Desktop opens an OAuth authorization request served entirely by the attacker's server, which can phish credentials or otherwise abuse the OAuth exchange.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-178)
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
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L46-59)
```typescript
export const getCredentialUrl = (cred: Map<string, string>) => {
  const u = cred.get('url')
  if (u) {
    return new URL(u)
  }

  const protocol = cred.get('protocol') ?? ''
  const username = cred.get('username')
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const host = cred.get('host') ?? ''
  const path = cred.get('path') ?? ''

  return new URL(`${protocol}://${user}${host}/${path}`)
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

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
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
