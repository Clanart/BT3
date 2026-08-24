## Title
Malicious Git Remote/Proxy Can Spoof `WWW-Authenticate` Header to Redirect Desktop's Native "Sign in" Flow to an Attacker-Controlled Endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

## Summary
The credential-helper trampoline classifies an authentication endpoint as GitHub `'enterprise'` purely by inspecting an attacker-influenceable `WWW-Authenticate` header forwarded by Git, without validating that the host is actually a GitHub/GHE instance. When this classification fires for an unrecognized host, Desktop launches its native "Sign in to GitHub Enterprise" dialog and calls `setSignInEndpoint(origin)` with `origin` set to the attacker's own remote host, prompting the user to authenticate directly against that attacker-controlled server inside what looks like Desktop's trusted, built-in sign-in UI.

## Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` (lines 137-179) determines how a credential request should be handled. Before making any real verification (e.g. `isGitHubHost`), it scans the credential map entries forwarded by Git for `wwwauth[...]` keys, which come from a `WWW-Authenticate` response header echoed by the remote server during authentication: [1](#0-0) 

If that value contains `realm="GitHub"`, the function immediately returns `'enterprise'` — fully attacker-controlled content decides the classification, since the credential helper protocol is fed directly by whatever the (possibly malicious) HTTP server for the git remote returns.

This classification is then used in `getCredential`: [2](#0-1) 

Because no account exists for this arbitrary host, and it isn't classified `'generic'`, Desktop calls `ui.promptForGitHubSignIn(endpoint)` where `endpoint` is derived from the credential's own host/URL (`getCredentialUrl(cred)`), i.e. the attacker's git remote host — not a genuine GitHub or GHE server.

In `trampolineUIHelper.promptForGitHubSignIn`: [3](#0-2) 

Since `hostname !== 'github.com'`, it takes the `else` branch: `beginEnterpriseSignIn(cb)` followed by `setSignInEndpoint(origin)`, where `origin` is the attacker's own domain — bypassing the normal manual "Enterprise address" entry step a user would otherwise type themselves, and instead auto-populating it with attacker-controlled data. This directly drives `SignInStore.setEndpoint`, which validates the URL syntactically/connectively and transitions straight to the `Authentication` step for that attacker endpoint: [4](#0-3) 

The resulting popup is shown with `isCredentialHelperSignIn: true` and `credentialHelperUrl: endpoint` (the attacker's URL), inside Desktop's native, trusted "Sign in" dialog UI (`app/src/ui/sign-in/sign-in.tsx`), which displays only a benign message ("Git requesting credentials to access <url>") without any strong warning that this is an unverified third-party host.

The broken invariant, mirroring the seed report's core issue, is: **a security-relevant scope/kind ("this is a trusted GitHub endpoint") is derived from mutable, attacker-controlled data (the `WWW-Authenticate` realm string returned by the remote) rather than from a value Desktop verifies itself, and this classification is never "spent down" or re-validated before being used to drive a sensitive UI/credential flow** — directly analogous to the `xToken.approve` case where the allowance amount was taken from an unverified/growing quantity instead of the actual entitled (verified) value.

## Impact Explanation
If a user clicks a malicious link or clones/fetches from an attacker-controlled or compromised git remote (or a MITM proxy on an insecure network) that returns a crafted `WWW-Authenticate: Basic realm="GitHub"` header on a 401 response, Desktop's own git credential helper will misclassify the endpoint as GitHub Enterprise and drive the user straight into its native sign-in dialog, pre-populated with the attacker's own host as the "Enterprise" endpoint. Because this happens inside GitHub Desktop's own first-party UI (not a browser popup or unfamiliar dialog), a user who is used to seeing this flow when authenticating to legitimate internal Enterprise servers may be misled into believing they're interacting with a real GHE instance and complete the credential-entry/sign-in step against attacker infrastructure, resulting in credential exfiltration to the attacker's server.

## Likelihood Explanation
Exploitation only requires the attacker to control the HTTP responses of a git remote the user is fetching/cloning from (matching the allowed threat model: "the attacker controls... a git remote/proxy response"). No local access, malware, or leaked credentials are required — only that git attempts HTTPS auth against the attacker's server and the server returns a header the trampoline's credential helper trusts blindly. However, likelihood is moderated because the user must still notice/complete the resulting sign-in dialog, and the `credentialHelperUrl` displayed does reveal the actual attacker URL (albeit not prominently flagged as suspicious).

## Recommendation
Do not classify an endpoint as `'enterprise'`/GitHub based solely on the `WWW-Authenticate` realm string. This heuristic should, at most, be used as a hint to decide whether to make a verification network call (`isGitHubHost`) rather than as a direct trust decision. Additionally, `promptForGitHubSignIn`/`setSignInEndpoint` should not silently auto-populate and jump past the "Enterprise address" entry step for endpoints that haven't been independently verified as genuine GitHub/GHE hosts; the UI should clearly flag that the host is unverified and require explicit user confirmation of the destination domain before any credential entry step is reached.

## Proof of Concept
1. Attacker sets up an HTTPS git server (e.g., using a self-signed cert accepted by the user, or a compromised legitimate mirror) at `https://evil.example.com/repo.git`.
2. Attacker configures the server to respond to unauthenticated Git-over-HTTP requests with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim, using GitHub Desktop, clones or fetches from `https://evil.example.com/repo.git` (e.g., via "Clone repository" using a URL shared by the attacker, or an existing remote pointed there).
4. Git invokes the desktop credential helper (`git-credential-desktop`), forwarding the `wwwauth[]=Basic realm="GitHub"` field to `createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind`, which returns `'enterprise'` per `app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`.
5. Since no account matches `evil.example.com`, `ui.promptForGitHubSignIn('https://evil.example.com')` fires, invoking `beginEnterpriseSignIn` + `setSignInEndpoint('https://evil.example.com')` per `app/src/lib/trampoline/trampoline-ui-helper.ts:87-93`, and Desktop's native Sign-in dialog opens in the `Authentication` step pointed at the attacker's host.
6. If the user completes sign-in believing this is a legitimate GHE prompt, their credentials/PAT entry flow is directed at the attacker's endpoint.

Note: I was unable to fully inspect `app/src/ui/lib/authentication-form.tsx` (the tool call to read it errored out due to a missing parameter, and the index does not appear to have it fully indexed), so I could not directly confirm from source whether the Authentication step in this scenario performs a direct Basic-Auth POST of username/password to the attacker endpoint or requires a browser-based OAuth redirect (which would reduce, but not eliminate, exposure). A Devin session with full filesystem access should read `app/src/ui/lib/authentication-form.tsx` and `app/src/lib/stores/sign-in-store.ts`'s authentication submission path to confirm exactly what credential material, if any, is transmitted to the attacker-controlled endpoint during this flow.

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
