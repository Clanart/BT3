### Title
Attacker-controlled `WWW-Authenticate` header can spoof "GitHub Enterprise" detection and redirect the sign-in flow to an untrusted host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind`, used by the trampoline credential helper to decide how to authenticate a Git HTTPS request, trusts a `wwwauth[]` field forwarded by Git — which is populated directly from the remote server's `WWW-Authenticate` response header — to classify an arbitrary host as GitHub Enterprise. This mirrors the "Otter Audits" bug class: a validation routine assumes it only needs to check an authoritative/expected source (an explicit account list / a signed field), but an attacker-controlled side channel (`remaining_accounts` in the Solana report, the `WWW-Authenticate` header here) that is *not* independently validated can be used to smuggle a value that flips the check's outcome.

### Finding Description
When Git requests credentials for an HTTPS remote, it forwards any `WWW-Authenticate` headers it received from the server as `wwwauth[N]` fields in the credential-helper protocol input. `getEndpointKind` uses this attacker-suppliable field as a "happy path" to decide the endpoint is a genuine GitHub Enterprise host, without any independent verification: [1](#0-0) 

Any HTTPS Git server the attacker controls (e.g., a repo cloned from a malicious or compromised remote, or a proxy/MITM-able host) can respond `WWW-Authenticate: Basic realm="GitHub"` and be classified `'enterprise'` — completely bypassing the safer `isGitHubHost` probe (which performs a real `/meta` request and checks for the `x-github-request-id` header) that is only reached as a fallback when this header is absent: [2](#0-1) 

Once classified as `'enterprise'`, `getCredential` checks whether any stored account matches `apiEndpoint`. Since the attacker's host is not a real GHE server, no account will match, so Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` then drives the user into a full GitHub Enterprise sign-in flow pointed at the attacker's endpoint: [4](#0-3) 

Critically, the endpoint used here bypasses `SignInStore.setEndpoint`'s normal path — `dispatcher.setSignInEndpoint(origin)` calls `_setSignInEndpoint`, which performs only syntactic validation (`validateURL` — HTTPS scheme check only, no connectivity/`isGitHubHost` verification): [5](#0-4) [6](#0-5) 

This is the same class of gap the external report describes: the "authoritative" check (`isGitHubHost`'s real network probe / the accounts list) is bypassed because a different, attacker-controlled input path (the `WWW-Authenticate` header / `remaining_accounts`) is trusted without being subjected to the same validation.

### Impact Explanation
If successful, the app presents the user a "Sign in to your GitHub Enterprise instance" dialog whose backing endpoint is entirely attacker-controlled, while the UI gives no strong indication that the detection heuristic (rather than a real GitHub server) drove this flow. Depending on which sub-flow the user chooses:
- Browser-based OAuth: `getOAuthAuthorizationURL(endpoint, ...)` opens a URL on the attacker's host, and any resulting "callback" is trusted as if it authenticated with GitHub Enterprise, allowing account/session confusion or phished credentials.
- Direct username/password (if offered for enterprise non-2FA sign-in), credentials would be sent directly to the attacker's server via Basic auth.

This can result in credential/token exfiltration and unauthorized OAuth/account binding, which is within the stated valid-impact criteria (attacker controls a git remote/proxy response driving credential/auth flow to an untrusted destination).

### Likelihood Explanation
The trigger is straightforward: a user adds or clones from a Git remote (any HTTPS host the attacker controls, e.g., a spoofed mirror, a compromised third-party Git server, or a MITM/proxy) that requires authentication and returns a spoofed `WWW-Authenticate: ... realm="GitHub"` header on a 401 response. No local access, no malware, and no unnatural user interaction beyond normal cloning/fetching from an attacker-supplied remote is required. Likelihood is moderated by the need for the user to then proceed through the resulting sign-in dialog, but the dialog itself is legitimately presented by Desktop, making it plausible for users to comply, similar to typical credential-phishing UX abuse.

### Recommendation
Do not treat the `wwwauth[]` realm value as authoritative for GitHub host classification. Either drop this heuristic and always fall back to the network-verified `isGitHubHost` check (`/meta` + `x-github-request-id` verification), or require that heuristic hits still be confirmed by `isGitHubHost` before initiating a sign-in flow. Additionally, `SignInStore.setEndpoint` / `_setSignInEndpoint` should perform (or reuse) the same connectivity/host verification before advancing past `EndpointEntry` to `Authentication`, rather than relying on syntactic URL validation alone.

### Proof of Concept
1. Attacker stands up an HTTPS Git server (or a proxy in front of one) at `https://evil.example.com`.
2. Victim adds this as a remote in GitHub Desktop and performs a fetch/clone requiring auth.
3. On the credential request, the attacker's server responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to the Desktop credential helper.
5. `getEndpointKind` returns `'enterprise'` for `evil.example.com` purely from this header (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`), with no matching stored account.
6. Desktop shows a "Sign in to your GitHub Enterprise instance" dialog whose endpoint is `https://evil.example.com`, and the user's subsequent authentication (browser OAuth redirect or credential submission) targets the attacker's server rather than a real GitHub Enterprise instance.

Note: I was unable to fully trace the exact UI form used at the `Authentication` step (whether it exposes direct username/password submission for enterprise accounts versus browser-only OAuth) due to index size limits on some UI component files; a Devin session with full repo access would be needed to confirm the exact credential-exfiltration surface at that step.

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

**File:** app/src/lib/api.ts (L2461-2483)
```typescript
  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

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
