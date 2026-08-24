Confirmed: `setEndpoint` in `sign-in-store.ts` only validates URL syntax/protocol (`validateURL` — must be `https:`), it does not require the host to actually be GitHub — it just proceeds to the `Authentication` step for any syntactically-valid HTTPS host. This confirms the phishing dialog is reachable for an attacker-controlled HTTPS host.

### Title
Tautological "is this a GitHub host" check lets a malicious git remote trigger a trusted-looking GitHub sign-in dialog pointed at the attacker's server - (File: app/src/lib/api.ts, app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
The report's broken invariant is: a "validation" value is derived from the very same untrusted source it is supposed to validate, so the source can trivially make the check pass. `isGitHubHost` in [1](#0-0)  reproduces this pattern for host-trust decisions instead of price data: it decides "is this remote a genuine GitHub/GHE host" by sending an HTTP request to that same host and checking whether the *response itself* contains the `x-github-request-id` header [2](#0-1) . Since the header comes from the server being classified, any attacker-controlled HTTPS server can simply add that header and the check always resolves to "yes, this is GitHub", exactly analogous to `minOutput` being computed from the same manipulable price feed it is meant to validate.

### Finding Description
`isGitHubHost(url)` is used by `getEndpointKind` in the Git credential-helper trampoline to classify an arbitrary remote/credential-URL host as `'enterprise'`, `'github.com'`, or `'generic'` [3](#0-2) . For unknown hosts it falls through to `isGitHubHost(endpoint)`, whose only remaining check is whether the attacker's own HTTP response carries `x-github-request-id` [2](#0-1) . There is no cryptographic proof, no CA-pinned identity check, and no reliance on data outside of the response the attacker fully controls — the "expected" and "actual" values collapse to the same attacker-controlled channel, just like the report's `router.getAmountsOut` being used both to compute the swap and to validate it.

When `getEndpointKind` returns anything other than `'generic'` and no existing account matches that endpoint, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` [4](#0-3) . `promptForGitHubSignIn` starts the Enterprise sign-in flow and calls `dispatcher.setSignInEndpoint(origin)` with the attacker's origin, then shows Desktop's native `SignIn` dialog [5](#0-4) . `SignInStore.setEndpoint` only checks URL syntax and that the protocol is `https:` via `validateURL` [6](#0-5) [7](#0-6)  — it does not independently re-verify that the endpoint is a real GitHub host, so it accepts whatever origin was derived from the tautological `isGitHubHost` check.

### Impact Explanation
An attacker who controls a git server that the user clones/fetches/pushes to over HTTPS (e.g., a malicious "corporate mirror," a compromised self-hosted Git server, or a spoofed enterprise Git host referenced by a shared clone URL) can respond to Desktop's discovery request with an `x-github-request-id` header. This flips Desktop's credential helper into believing the host is a trusted GitHub Enterprise instance and pops the built-in, first-party-styled "Sign in to GitHub Enterprise" dialog (`SignIn` / `SignInEnterprise` UI) with the credential-helper endpoint pointed at the attacker's server. Because this dialog is rendered by Desktop itself (not a webview controlled by the remote), it carries higher user trust than a browser phishing page. Depending on the sign-in path (browser OAuth redirect or PAT-based enterprise auth), this can lead to the OAuth authorization code/PAT being sent to the attacker's endpoint, i.e., credential/token exfiltration or unauthorized account binding to an attacker-controlled "enterprise" endpoint.

### Likelihood Explanation
The attacker only needs to control the HTTP(S) response of a git remote the user adds/fetches from — a normal, unprivileged capability for anyone who can convince a user to clone/fetch from their server (no admin rights, no local access, no leaked credentials, no malware already present). Setting one static response header is trivial and requires no exploitation of Desktop internals beyond triggering a credential-helper lookup, which happens naturally on any authenticated git operation against that remote.

### Recommendation
Do not derive host trust from data returned by the host being evaluated. `isGitHubHost`/`getEndpointKind` should rely on an allowlist of known GitHub/GHE endpoints, a signed/pinned identity mechanism (e.g., validated against `api.github.com`/configured GHE endpoints only, or an out-of-band GitHub Connect/enterprise registration step), rather than a self-reported response header. At minimum, `promptForGitHubSignIn`/`setEndpoint` should require explicit user confirmation that clearly discloses the exact untrusted origin being contacted before initiating any enterprise sign-in flow triggered implicitly by a credential-helper request, so users aren't silently funneled into an auth flow against a server they didn't knowingly choose to trust.

### Proof of Concept
1. Attacker sets up an HTTPS git server at `https://malicious-git.example` and configures it to add the response header `x-github-request-id: 1` to any HTTP response (including the `/meta` HEAD probe Desktop issues).
2. Victim adds this remote in GitHub Desktop (e.g., clones or adds an existing repo pointing at that remote) and performs a fetch/pull, which invokes the trampoline credential helper.
3. `getCredential` → `getEndpointKind` → `isGitHubHost('https://malicious-git.example/...')` sends a HEAD request to `https://malicious-git.example/meta?ghd=<uuid>`; the attacker's server returns 200 with the forged `x-github-request-id` header, and `isGitHubHost` returns `true` [2](#0-1) .
4. Since no account exists for that endpoint, Desktop calls `ui.promptForGitHubSignIn` and opens its native GitHub Enterprise sign-in dialog with `credentialHelperUrl` set to the attacker's URL [4](#0-3) [8](#0-7) .
5. The user, trusting the native Desktop dialog, proceeds with sign-in, and the OAuth/PAT flow is directed at the attacker-controlled "enterprise" endpoint.

### Citations

**File:** app/src/lib/api.ts (L2429-2491)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

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
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-435)
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
