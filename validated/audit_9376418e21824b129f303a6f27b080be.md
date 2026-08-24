## Title
Overly-broad hostname heuristic in `isGitHubHost` lets an attacker-controlled git remote trigger a spoofed "Sign in to GitHub Enterprise" flow bound to the attacker's server - (File: `app/src/lib/api.ts`)

### Summary
`isGitHubHost()` is used by the Git credential-helper trampoline to decide whether a remote a user is fetching/pushing/cloning from is a GitHub(.com/Enterprise) endpoint that should trigger Desktop's built-in sign-in UI, or a "generic" host that should fall back to the plain credential prompt. One of its checks is a naive regex, `/(^|\.)(github)\./`, which matches any hostname where the label `github` is immediately followed by a dot anywhere in the name — not just real GitHub subdomains. A malicious server the user is cloning/fetching from can pick a hostname like `github.attacker.example.com` and get classified as a trusted GitHub Enterprise host *before* the function ever performs its real verification (the `x-github-request-id` HTTP check). This is structurally the same flaw as the ERC1820 report: a function meant to gate trust ("do you implement/represent GitHub?") returns an affirmative answer far more permissively than it should, based on a superficial pattern match rather than actual proof.

### Finding Description
`isGitHubHost(url)` in [1](#0-0)  short-circuits to `true` if the hostname matches `/(^|\.)(github)\./.test(hostname)` before it ever performs the "best-effort" network verification later in the function (the `x-github-request-id` header check at lines 2467-2483). This regex only requires a `github.` label to appear at the start of the hostname or right after a dot — it does not require the label to be at the end of the domain (i.e., it does not anchor against the real registrable domain). Hostnames fully controlled by an attacker, such as `github.attacker.com`, `git.github.evilhost.io`, or `api.github.something-else.net`, all satisfy this pattern and are accepted as "GitHub hosts" without ever making the network call that would otherwise disprove it.

This function is consumed directly by the trampoline git-credential helper's `getEndpointKind` in [2](#0-1) , whose final fallback is:
```
return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
```
`endpoint` here is derived directly from the URL of the git remote being fetched/cloned/pushed — fully attacker-controlled if the user adds or is directed to such a remote (e.g., via a malicious clone URL, a compromised proxy, or a repository's configured submodule/remote URL).

When `getCredential()` in the same file (lines 93-135) is invoked by Git for that remote, and no existing account already matches the (real) API endpoint, it takes the branch:
```
if (endpointKind !== 'generic' && !accounts.some(a => a.endpoint === apiEndpoint)) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
  ...
}
```
`promptForGitHubSignIn` in [3](#0-2)  then calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` where `origin` is the attacker's hostname, and shows the sign-in popup with `credentialHelperUrl: endpoint` (rendered verbatim in [4](#0-3)  as "Git requesting credentials to access…"). Because the enterprise sign-in flow was already given a validated endpoint (skipping the normal `EndpointEntry`/`validateURL` step a manual sign-in would go through), Desktop proceeds straight to `SignInStep.Authentication` for that attacker endpoint, per `setEndpoint`'s logic in [5](#0-4) .

If the user clicks "Continue with browser" (the only available action at that step), `authenticateWithBrowser()` in [6](#0-5)  opens `getOAuthAuthorizationURL(endpoint, csrfToken)` in the system browser, and on completion exchanges the returned code with `requestOAuthToken(endpoint, code)` in [7](#0-6) , which POSTs Desktop's `ClientID`/`ClientSecret` to the attacker-controlled `endpoint`. This sends the app's OAuth secret to a server the user never intentionally chose to trust as "GitHub Enterprise" — Desktop chose it for them based on the flawed `isGitHubHost` heuristic.

### Existing guards and why they don't stop this
- `isDotCom`/`isGHE` checks (lines 2443-2445) only match exact `github.com`/`api.github.com`/`*.ghe.com` — they correctly reject the spoofed host, but control then falls to the vulnerable regex.
- `isKnownThirdPartyHost` (lines 2407-2427) only blocklists a fixed set of known competitors (`gitlab.com`, `bitbucket.org`, etc.) and does nothing to prevent a `github.`-prefixed attacker domain from matching the next check.
- The "real" verification (the `x-github-request-id` HTTP HEAD probe) is the only check that actually confirms the remote is a genuine GitHub server, but it is never reached because the regex check above it already returned `true`.
- `validateURL()` (used only in the manual `EndpointEntry` sign-in step) is bypassed entirely in this automatic flow, since the trampoline path jumps straight into `SignInStep.Authentication` with a pre-set endpoint.

### Impact Explanation
An attacker who controls a git remote (or a MITM proxy answering git's HTTP(S) requests) that the victim clones/fetches/pushes to can, purely through hostname choice, cause GitHub Desktop to present its native "Sign in to GitHub Enterprise"/credential-helper dialog scoped to the attacker's server. If the user proceeds, this results in:
- Desktop's OAuth `ClientID`/`ClientSecret` being transmitted to the attacker's server during token exchange (credential/secret exfiltration), and
- The victim's account effectively becoming "bound" in Desktop to an attacker-operated endpoint under the guise of a legitimate GitHub Enterprise sign-in (unauthorized account binding), which the app itself vouched for by unconditionally treating the host as GitHub-affiliated.

This falls squarely within the "unauthorized OAuth or account binding" impact category: the vulnerability is triggered purely by cloning/fetching a repository from an attacker-chosen URL, with no admin rights, no pre-existing credentials, and no unnatural steps beyond the normal act of authenticating when Git/Desktop prompts for it (which is expected behavior for a private remote).

### Likelihood Explanation
Likelihood is moderate: the attacker only needs to host a git server (or intercepting proxy) on a domain containing a `github.` label and have the victim add/fetch it as a remote — a very low bar, and choosing such a domain name is itself a plausible social-engineering-adjacent but purely technical trigger (no phishing steps required to reach the vulnerable code path; the flaw is purely in Desktop's classification logic). The subsequent OAuth step still requires the user to click "Continue with browser" and complete authentication in their real browser, which is a normal, expected action when Desktop legitimately prompts for enterprise credentials — nothing about the UI indicates anything is wrong, since the credential-helper dialog only shows the raw URL as a footnote.

### Recommendation
- Anchor the `github.` heuristic to the actual registrable domain (e.g., require the hostname to end with `.github.com`/`.ghe.com`/`.githubenterprise.com` or use a proper public-suffix-aware comparison) instead of a substring/label regex that matches anywhere in the hostname.
- Do not allow the heuristic label-match branches (`github.`, `bitbucket.`/`gitlab.`) to short-circuit before the authoritative network verification (`x-github-request-id`); at minimum, require the network check to succeed before ever setting `endpointKind = 'enterprise'` for hosts that aren't `isDotCom`/`isGHE`.
- When the trampoline credential helper auto-initiates a GitHub Enterprise sign-in flow (bypassing manual `EndpointEntry`), route the endpoint through the same `validateURL` scrutiny and clearly warn the user in the sign-in dialog that the endpoint was inferred automatically from the git remote, not entered by them.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or MITM proxy) at `https://github.totally-not-github.com/victim-project.git` that does not send `x-github-request-id`.
2. Victim runs `git clone https://github.totally-not-github.com/victim-project.git` inside GitHub Desktop (or adds it as a remote and fetches).
3. Git's credential helper invokes Desktop's trampoline `getCredential`. `getEndpointKind` reaches the fallback `isGitHubHost(endpoint)` check; the hostname `github.totally-not-github.com` matches `/(^|\.)(github)\./`, so `isGitHubHost` returns `true` without ever making the verification request.
4. `endpointKind` is `'enterprise'`; since no account exists for that endpoint, `promptForGitHubSignIn('https://github.totally-not-github.com')` is invoked, opening the "Sign in" dialog with `credentialHelperUrl` set to the attacker's URL and the sign-in store already at `SignInStep.Authentication` for that endpoint.
5. If the victim clicks "Continue with browser," `authenticateWithBrowser()` opens `getOAuthAuthorizationURL('https://github.totally-not-github.com', csrfToken)` and on callback POSTs Desktop's OAuth `ClientID`/`ClientSecret` to `https://github.totally-not-github.com/login/oauth/access_token` via `requestOAuthToken`, sending the secret to the attacker and treating whatever token the attacker's server returns as a signed-in "GitHub Enterprise" account.

Note: I was not able to fully trace how the OAuth `client_secret` value is provisioned/scoped at runtime (e.g., whether it's a fixed embedded secret shared across all Enterprise sign-ins or something endpoint-specific) within the indexed code; this affects exact severity of the secret-exfiltration sub-claim and would benefit from a full-repo Devin session to confirm.

### Citations

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

**File:** app/src/lib/api.ts (L2429-2454)
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

**File:** app/src/lib/stores/sign-in-store.ts (L260-303)
```typescript
  public async authenticateWithBrowser() {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.Authentication &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with browser authentication`
      )
    }

    this.setState({ ...currentState, loading: true })

    if (currentState.kind === SignInStep.ExistingAccountWarning) {
      const { existingAccount } = currentState
      // Try to avoid emitting an error out of AccountsStore if the account
      // is already gone.
      if (this.accounts.find(x => x.endpoint === existingAccount.endpoint)) {
        await this.accountStore.removeAccount(existingAccount)
      }
    }

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
