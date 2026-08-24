### Title
Spoofed `WWW-Authenticate` realm on any git remote triggers the GitHub Enterprise sign-in dialog against an attacker-controlled endpoint - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
GitHub Desktop's Git credential-helper trampoline decides whether an unknown remote is a GitHub-flavored host ("enterprise") purely by inspecting the `WWW-Authenticate` header Git captured from the server's HTTP response. If that header claims `realm="GitHub"`, Desktop treats the host as a real GitHub Enterprise instance and pops the native "Sign in to GitHub Enterprise" dialog pointed directly at the remote's own origin — without any independent verification that the host is actually GitHub software. This is the same class of bug as CVE-2021-22926: a value that should only ever be treated as "trusted, verified identity" (the keychain nickname vs. a local file; here, "this is a genuine GitHub Enterprise host" vs. "attacker-declared realm string") is accepted from an untrusted, attacker-controlled source and used to select a security-sensitive code path.

### Finding Description
`getEndpointKind` in [1](#0-0)  classifies a credential request's endpoint as `'enterprise'` solely based on a `wwwauth[...]` field containing `realm="GitHub"`, which is copied verbatim from the git server's HTTP response headers by Git itself and forwarded to the credential helper — this value is fully attacker-controlled by whoever is behind the remote/proxy the user is fetching or pushing from.

When `getCredential` runs [2](#0-1) , if the endpoint is classified as non-`'generic'` and there's no existing account for that endpoint, it calls `ui.promptForGitHubSignIn(endpoint)` where `endpoint` is the attacker's own remote URL.

`promptForGitHubSignIn` in [3](#0-2)  then calls `dispatcher.beginEnterpriseSignIn(cb)` followed by `dispatcher.setSignInEndpoint(origin)` — programmatically feeding the attacker's origin into the sign-in flow, bypassing the normal manual "enter your Enterprise server URL" step that a user would otherwise see and could scrutinize.

`SignInStore.setEndpoint` in [4](#0-3)  validates the URL is syntactically fine and HTTPS, then transitions straight to the `Authentication` step for that attacker-supplied endpoint. The resulting dialog looks exactly like a legitimate "Sign in to your GitHub Enterprise" prompt (same as `SignInStep.Authentication` handled by `AuthenticationForm`), but any username/password or PAT entered will be sent to the attacker-controlled host, not GitHub.

The invariant that's broken: "we only prompt the native GitHub sign-in UI (and therefore only send credentials) for hosts we've verified to actually be GitHub/GHE" — but the verification is a self-reported HTTP header from the very server the user is authenticating to, with no cross-check (e.g., hitting `/meta` or checking `x-github-request-id`, as `isGitHubHost` does in the fallback path a few lines later at line 178).

### Impact Explanation
A malicious git server (or a MITM/rogue proxy on an HTTP, not HTTPS-verified-cert, remote) that the victim clones/fetches from can spoof `WWW-Authenticate: Basic realm="GitHub"` on a 401 response. This causes GitHub Desktop to show its trusted "Sign in to GitHub Enterprise" chrome pointed at the attacker's host. Since Desktop's UI presents this identically to a real Enterprise sign-in, a user who has previously used Enterprise instances (or doesn't scrutinize the host) may enter real GitHub Enterprise (or personal access token) credentials, which are then sent to the attacker's server — a credential phishing/exfiltration primitive triggered purely by fetching/cloning an attacker-supplied repository/remote, satisfying the "attacker controls a git remote/proxy response" impact criterion.

### Likelihood Explanation
Exploitation requires only that the victim performs an ordinary `fetch`/`pull`/`push`/`clone` against a remote the attacker controls (or a proxy/MITM position on a plain-HTTP or otherwise interceptable connection) and that the credential-fill request 401s with the crafted header — no additional user interaction beyond normal git usage, no admin rights, and no local file/malware needed. The main mitigating factor is that many users will notice the host isn't one they recognize before entering real credentials, and Desktop does have a secondary `isGitHubHost` heuristic [5](#0-4)  for the case where no `wwwauth` header path applies — but the `wwwauth` shortcut at lines 157–165 bypasses that stronger check entirely whenever the header is present.

### Recommendation
Do not trust the `wwwauth[...]` realm string alone to classify a host as GitHub/Enterprise. Require the same server-side confirmation used in the fallback branch (`isGitHubHost`, which presumably checks for GitHub-specific response signals via `getAPIEndpoint`/`api.ts`) before offering the native sign-in flow, or at minimum surface the actual endpoint URL prominently in the credential-helper sign-in dialog and require explicit user confirmation of the host (restoring the `EndpointEntry` step instead of auto-populating it via `setSignInEndpoint`) before soliciting Enterprise credentials.

### Proof of Concept
1. Stand up an HTTP(S) git server that, on any authenticated request (e.g., `git-upload-pack`), returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone/fetch this remote as a new repository (no existing account matches this endpoint).
3. Desktop's trampoline credential helper calls `getEndpointKind`, matches the `realm="GitHub"` regex, classifies the endpoint as `'enterprise'` [6](#0-5) .
4. Since no account exists for this endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` = attacker's URL, which immediately opens the "Sign in to your GitHub Enterprise" dialog authenticating against the attacker's origin [7](#0-6) .
5. Any credentials the user submits are POSTed to the attacker's server.

Note: I was not able to fully trace the exact HTTP call made by the `AuthenticationForm`/basic-auth submission path (only `resolveOAuthRequest`/OAuth token exchange was visible in the excerpts retrieved), so I cannot confirm from local code alone whether the basic-auth (username/password) submission path performs any additional endpoint-identity validation before sending credentials. If a session with full file access is available, verifying `authentication-form.tsx` and the basic-auth API call in `api.ts` would confirm the exact credential-transmission mechanics.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-178)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-458)
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
```
