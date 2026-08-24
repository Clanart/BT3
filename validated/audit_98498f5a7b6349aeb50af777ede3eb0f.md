## Title
Malicious git server can spoof a GitHub Enterprise identity via the `WWW-Authenticate` realm and trigger a phishing-style OAuth sign-in - (`app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The bug class in the external report is "a check is performed, but the code trusts attacker-influenced input to satisfy that check and grants a privileged action beyond what was intended" (allowance checked but not enforced/updated, so it can be abused repeatedly). The closest verified Desktop analog is in the credential-helper trampoline's endpoint classification logic: it derives whether a remote counts as a "GitHub"/"Enterprise" host largely from a `WWW-Authenticate` header supplied by the remote git server itself, and then uses that classification to automatically open a GitHub-Enterprise sign-in flow pointed at the attacker's origin.

### Finding Description
When Git needs credentials for an HTTP(S) remote, it invokes Desktop's credential helper and forwards any `WWW-Authenticate` headers returned by the *remote server* as `wwwauth[]=...` fields on stdin. `getEndpointKind` in [1](#0-0)  classifies the endpoint as `'enterprise'` purely because the (attacker-controlled) server's response contains `realm="GitHub"`, without any relation to the actual `apiEndpoint`/host being a real GitHub Enterprise instance:

```
for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } ...
```

That classification then flows into `getCredential`, which — because no account exists for this unknown host — calls `ui.promptForGitHubSignIn(endpoint)` [2](#0-1) .

`promptForGitHubSignIn` automatically starts an Enterprise sign-in flow and sets the sign-in endpoint to the attacker's own `origin` (taken directly from the credential URL, i.e. the malicious remote) without requiring the user to type it in manually:
```
} else {
  this.dispatcher.beginEnterpriseSignIn(cb)
  await this.dispatcher.setSignInEndpoint(origin)
}
this.dispatcher.showPopup({ type: PopupType.SignIn, isCredentialHelperSignIn: true, credentialHelperUrl: endpoint })
``` [3](#0-2) 

`setEndpoint`/`authenticateWithBrowser` in the sign-in store then open the system browser directly at `https://<attacker-origin>/login/oauth/authorize?client_id=...` [4](#0-3) , and `setEndpoint` performs only syntactic/connectivity validation of the URL [5](#0-4)  — nothing verifies the host is an actual GitHub/GHE instance.

The corrupted value here is the *trust classification* of the remote endpoint (`enterprise` vs `generic`): it is derived from server-supplied data (`WWW-Authenticate: ... realm="GitHub"`) rather than from any Desktop-side determination (`isGitHubHost` result, existing account, or explicit user entry), and this classification is used to automatically drive the user into Desktop's own "Sign in" UI pointed at an attacker-chosen origin.

### Impact Explanation
This mirrors the ERC5095 pattern: a check ("is this a GitHub/GHE host that should get a GitHub sign-in prompt?") exists but is satisfiable by attacker-controlled input, and once satisfied the app performs a privileged, trust-conferring action (opening Desktop's official-looking "Sign in to GitHub" dialog and driving the OAuth browser flow) toward a location the attacker fully controls. If the attacker's server simultaneously runs a fake OAuth/login page that mimics GitHub's UI, an unsuspecting user completing the flow believes they are authenticating to a legitimate Enterprise instance recognized by Desktop, but are instead interacting with attacker infrastructure — enabling credential/token phishing. This can be triggered simply by adding/cloning/fetching from a malicious remote (fully within the "attacker controls a git remote/proxy response" impact class), with no local access or prior compromise required.

### Likelihood Explanation
Likelihood is moderate: the attacker must control (or MITM) the HTTP git server that Desktop's credential helper talks to, and must get the victim to perform a Git operation (clone/fetch/push) against it — which is a normal, expected user action and requires no unusual steps. The `WWW-Authenticate` header is standard HTTP and fully attacker-controlled; no additional user interaction beyond the normal "authenticate to this repo" flow is required to reach `promptForGitHubSignIn`.

### Recommendation
Do not derive trust/classification of a remote endpoint solely from server-supplied `WWW-Authenticate` realm strings. At minimum:
- Require that `setSignInEndpoint`/`beginEnterpriseSignIn` triggered via the credential-helper path validate the target host against `isGitHubHost` (an actual network check) before trusting the `realm="GitHub"` hint, rather than treating the header as sufficient on its own in `getEndpointKind`.
- Surface the origin to the user before opening any OAuth browser flow initiated automatically via the credential helper (e.g., an explicit confirmation dialog showing the exact host being signed into), rather than silently calling `setSignInEndpoint(origin)`.
- Consider only trusting `wwwauth[]` realm hints for hosts already known/added as GHE accounts, falling back to the generic-git-auth prompt otherwise.

### Proof of Concept
1. Stand up an HTTP git server (e.g., serving `git-http-backend`) that, on an authentication challenge, responds with `WWW-Authenticate: Basic realm="GitHub"`.
2. Have the victim add this server as a remote (e.g., clone it, or add it as a remote to an existing repo) and perform a `fetch`/`push` in Desktop.
3. Git invokes Desktop's credential helper; `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:137-165`) returns `'enterprise'` purely from the header.
4. Since no account matches this endpoint, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)`, which calls `beginEnterpriseSignIn` + `setSignInEndpoint(origin)` with the attacker's origin, then shows the `SignIn` popup.
5. If the victim proceeds, `authenticateWithBrowser` opens the system browser at `https://attacker-origin/login/oauth/authorize?...`, which the attacker can serve as a phishing page mimicking GitHub's OAuth authorize/login screen.

Note: I could not fully trace whether `isGitHubHost`/`setSignInEndpoint` performs any additional network-based verification that might reduce (but not eliminate) exploitability before the popup is shown; this would need to be checked in a live session for full confirmation of the reachable path end-to-end.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-165)
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-437)
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
```
