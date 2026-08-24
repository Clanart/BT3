Confirmed: `wwwauth[]` entries in the credential map come directly from Git's credential-fill protocol, which populates them from the `WWW-Authenticate` HTTP response headers returned by the remote server during an HTTPS auth challenge [1](#0-0) . This is server-controlled input reaching `getEndpointKind` unauthenticated and unvalidated [2](#0-1) .

### Title
Attacker-controlled `WWW-Authenticate` realm header triggers unauthorized GitHub sign-in binding to a spoofed remote endpoint - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
GitHub Desktop's git credential helper trampoline decides whether a remote host should be treated as a trusted "GitHub" endpoint (`enterprise`/`github.com`) partly by inspecting the `WWW-Authenticate` header Git receives from the remote server during HTTPS authentication. This header is emitted by whatever server the user's git operation talks to — i.e., it is attacker-controlled whenever the user clones/fetches/pushes to a malicious or MITM'd non-GitHub remote. If the header contains `realm="GitHub"`, Desktop unconditionally classifies the arbitrary attacker host as an `enterprise` GitHub endpoint, which — because no account exists for that host — automatically triggers the enterprise "Sign in to GitHub" OAuth flow (`beginEnterpriseSignIn` + `setSignInEndpoint`) pointed at the attacker's domain, and any account object the attacker's fake OAuth/token endpoint responds with is trusted and stored in Desktop's account list.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` is used by `getCredential` (the `git credential fill` implementation) to decide how to authenticate a remote request [3](#0-2) . Before making any independent verification, it trusts a `WWW-Authenticate` header value forwarded from Git:

```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
``` [2](#0-1) 

This header comes straight from the HTTP response of whatever server git is talking to for that operation (the `wwwauth[]` credential field is Git's own mechanism for forwarding captured `WWW-Authenticate` headers to the credential helper, confirmed by the parsing/format round-trip tests) [1](#0-0) . There is no verification that the host is actually GitHub (e.g., no TLS/cert pinning, no reachability check against the real GitHub API as is done later for the non-header fallback path via `isGitHubHost`) [4](#0-3) .

Once `endpointKind` resolves to `'enterprise'` and no stored account matches that host, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` being the attacker's own URL [5](#0-4) . `promptForGitHubSignIn` then drives the enterprise sign-in flow, calling `dispatcher.setSignInEndpoint(origin)` with the attacker's origin [6](#0-5) . This proceeds through `SignInStore.setEndpoint`, which only checks that the URL is syntactically HTTPS (`validateURL`) — it does not check that the host is actually a real GitHub Enterprise instance — before opening a browser to `getOAuthAuthorizationURL(endpoint, csrfToken)` on the attacker's server and later exchanging any resulting `code` for a token by POSTing to the same attacker-controlled `endpoint` [7](#0-6) [8](#0-7) . Since the attacker's server fully controls both the "authorization" step and the token/user-fetch responses, it can supply an arbitrary account object that Desktop then stores as a legitimate signed-in account.

This mirrors the root cause pattern in the source report: a security-relevant decision (here, "is this a trusted GitHub endpoint that should trigger our privileged sign-in flow") is made by trusting a value that should never be treated as authoritative for that decision, and no independent guard (equivalent to `onlyTimeLocker`) is enforced along that path.

### Impact Explanation
An attacker who controls a git remote/proxy (e.g. a malicious hosting service, compromised mirror, or MITM on a non-TLS-pinned connection) that a user clones/fetches/pushes to can, purely from the server side, force GitHub Desktop to open the full GitHub Enterprise OAuth/sign-in dialog against that attacker's arbitrary hostname. Since the resulting "account" is derived entirely from data the attacker's server chooses to return, this enables unauthorized OAuth flow initiation and account binding — the user could be led to authorize/enter Enterprise credentials against a domain Desktop presents as a legitimate "Sign in to GitHub" step, or the attacker can seed a fabricated account entry into Desktop's trusted account store, which subsequent credential/trust decisions in the same file rely on (`findGitHubTrampolineAccount`, `accounts.some(a => a.endpoint === apiEndpoint)`).

### Likelihood Explanation
The trigger requires no local access, malware, or leaked credentials — only that the victim performs a normal git network operation (clone/fetch/push) against a repository/remote the attacker controls or can intercept, and that the HTTP response includes a crafted `WWW-Authenticate: ... realm="GitHub"` header, which is trivial for any server operator to add.

### Recommendation
Do not derive the `enterprise`/`github.com` classification, or trigger the privileged sign-in flow, solely from an unauthenticated `WWW-Authenticate` header value. At minimum, require the same independent verification used in the fallback path (`isGitHubHost`, an authenticated round trip to the claimed endpoint) before trusting a `realm="GitHub"` claim, and/or gate `promptForGitHubSignIn` on the endpoint being reachable and identifiable as a genuine GitHub API before initiating OAuth/account-binding for it.

### Proof of Concept
1. Attacker stands up an HTTPS server (or MITMs an insecure connection) serving as a git remote, e.g. `https://evil.example.com/foo.git`.
2. Victim runs `git clone`/`fetch`/`push` against that remote from within GitHub Desktop.
3. When Git requests credentials over HTTPS, the attacker's server responds with `401` plus header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[]=Basic realm="GitHub"` to Desktop's credential helper via `command.stdin`.
5. `getEndpointKind` matches `realm="GitHub"` and returns `'enterprise'` [9](#0-8) .
6. Since no account exists for `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com')`, and Desktop begins the Enterprise sign-in flow against the attacker's domain, opening the system browser to the attacker's chosen "OAuth authorize" URL and later exchanging the `code` with the attacker's server for a token/account of the attacker's choosing [6](#0-5) [8](#0-7) .

### Citations

**File:** app/test/unit/git/credential-test.ts (L10-18)
```typescript
    it('expands arrays into numeric entries', async () => {
      assert.deepStrictEqual(
        [...parseCredential('wwwauth[]=foo\nwwwauth[]=bar').entries()],
        [
          ['wwwauth[0]', 'foo'],
          ['wwwauth[1]', 'bar'],
        ]
      )
    })
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-135)
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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-178)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
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
