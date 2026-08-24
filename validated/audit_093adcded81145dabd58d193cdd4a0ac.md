### Title
Attacker-controlled `WWW-Authenticate` realm spoofs GitHub Enterprise sign-in prompt for arbitrary git remotes - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` in the git credential-helper trampoline classifies a remote host as `'enterprise'` (i.e., "this is a GitHub-flavored server") purely by pattern-matching the `WWW-Authenticate` header that the remote HTTP server returns, before falling back to any independent validation. [1](#0-0) 

### Finding Description
Similar to the Notional bug where the code checked one allowance source (`nTokenWhitelist`) and reverted/short-circuited instead of falling through to the correct, more specific check (`nTokenAllowance`), Desktop's `getEndpointKind` short-circuits on a *weak, attacker-suppliable signal* (the `wwwauth[...]` header value that git forwards from the remote's 401 response) and returns `'enterprise'` immediately, before it ever performs the authoritative check `isGitHubHost()` (which does an actual `x-github-request-id` probe against the host): [2](#0-1) 

Git passes through the `WWW-Authenticate` response header from any HTTP(S) remote as `wwwauth[N]` credential-helper input keys — this value is fully controlled by whatever server responds to the clone/fetch/push request (a malicious or compromised third-party git host, or a MITM'd proxy for an HTTP remote). By simply returning `WWW-Authenticate: Basic realm="GitHub"` on a 401 response, an attacker-controlled remote at any arbitrary hostname (e.g. `evil.example.com`) causes `getEndpointKind` to classify itself as `'enterprise'`, completely bypassing the legitimate `isGitHubHost()` network-probe validation used in the fallback path (line 178) and the `isKnownThirdPartyHost` denylist that `isGitHubHost` applies. [3](#0-2) 

Once `endpointKind !== 'generic'` and no existing account matches that host, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)`, which opens the real Desktop "Sign in to GitHub Enterprise" dialog and initializes the sign-in flow against the attacker's arbitrary hostname: [4](#0-3) [5](#0-4) 

This is the "wrong allowance path taken" analog: the code should defer to the authoritative `isGitHubHost()` check (or at minimum treat the header hint as advisory, not authoritative) instead of trusting a value the remote server fully controls.

### Impact Explanation
The victim, while doing a normal `git clone`/`fetch`/`push` against a URL they typed or a remote configured in a cloned repository, is shown a legitimate-looking native "Sign in to GitHub Enterprise" dialog for a host that is not actually a GitHub/GHE server. `beginEnterpriseSignIn` + `setSignInEndpoint(origin)` sets the sign-in target directly to the attacker's `origin`: [6](#0-5) 
If the enterprise sign-in flow supports the basic-auth username/password path (not just browser OAuth), the user could be prompted to enter/confirm real corporate GHE credentials that are then sent to the attacker's server as part of establishing the "account," resulting in credential exfiltration. Even when only the OAuth/browser flow is used, this still causes an unexpected authentication prompt (UI spoofing) tied to an untrusted host, which can be leveraged for phishing or to trick the user into associating an unrelated real account/token with the attacker's endpoint.

### Likelihood Explanation
This requires only that the user clone or fetch/push against a repository whose remote HTTP server responds with a crafted `WWW-Authenticate` header — no local access, no prior malware, no leaked credentials. Any repo owner (or a MITM position on an `http://` remote) can trivially serve this header. This matches the allowed threat model: "attacker controls ... a git remote/proxy response."

### Recommendation
Do not treat the `wwwauth[...]` `realm="GitHub"` heuristic as authoritative. Use it only as a hint to prioritize/order checks, but always require the network-based `isGitHubHost()` verification (or an equivalent authenticated check) before classifying an unknown host as `'enterprise'` and before triggering the `promptForGitHubSignIn` UI. Analogous to the recommended nToken fix — check the strong/authoritative source first, and only fall back to the weaker heuristic when the authoritative check is inconclusive (e.g., network timeout), not the other way around.

### Proof of Concept
1. Host an HTTP(S) git server (e.g. via `git http-backend`) at `https://evil.example.com/pwned.git` that returns `401` with header `WWW-Authenticate: Basic realm="GitHub"` for unauthenticated requests.
2. In GitHub Desktop, clone `https://evil.example.com/pwned.git` (or add it as a remote to an existing repo and fetch).
3. Git invokes the credential helper (`trampoline-credential-helper.ts`) with `wwwauth[0]=Basic realm="GitHub"` among the credential fields.
4. `getEndpointKind` matches the regex/`includes` check on line 159 and returns `'enterprise'` without ever calling `isGitHubHost()`. [7](#0-6) 
5. Since no account exists for `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com/...')`, popping the native "Sign in to GitHub Enterprise" dialog bound to that attacker-controlled origin. [8](#0-7)

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

**File:** app/src/lib/api.ts (L2429-2463)
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
