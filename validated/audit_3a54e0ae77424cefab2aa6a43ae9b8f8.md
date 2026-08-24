## Title
Attacker-controlled `WWW-Authenticate` realm header spoofs a GitHub Enterprise host and triggers an OAuth-style sign-in prompt for a non-GitHub server - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

## Summary
This is a Desktop analog of the Sherlock report's core defect class: a piece of code that classifies an input based on an incomplete/naïve heuristic, and the un-handled/mis-handled branch silently drives sensitive downstream behavior with the wrong assumption baked in. In PeaPods, `IS_PAIRED_LENDING_PAIR` is a boolean that doesn't distinguish "regular fraxlend paired token" from "podded fTKN," so the swap path is fed the wrong `_swapOutputTkn`. In Desktop, `getEndpointKind()` classifies an arbitrary remote endpoint as `'enterprise'`/`'github.com'`/`'generic'` using several heuristics, one of which trusts a value that is **entirely attacker-controlled**: the `WWW-Authenticate` realm sent by the remote Git host.

## Finding Description
When Git performs HTTP authentication, it forwards any `WWW-Authenticate` response headers it captured to the credential helper as `wwwauth[N]` entries. `getEndpointKind()` uses this as a "happy path" to decide whether a host is GitHub before even checking whether an existing account matches: [1](#0-0) 

```
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } ...
```

This header is fully controlled by whatever server Git is talking to — this includes a malicious/compromised third-party remote the user added, a redirected clone/fetch target, a proxy, or a spoofed submodule URL — none of which requires any local access, admin rights, or pre-existing malware, matching the "attacker controls...a git remote/proxy response" impact criteria.

Once `getEndpointKind` returns `'enterprise'`, `getCredential()` treats the request as a legitimate GitHub host lookup: [2](#0-1) 

Since no existing `Account` has this attacker endpoint stored, the code calls `ui.promptForGitHubSignIn(endpoint)` with the **attacker-controlled endpoint** as the sign-in target: [3](#0-2) 

`promptForGitHubSignIn` then unconditionally starts the enterprise sign-in flow against that attacker origin: [4](#0-3) 

```
      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }
```

`_setSignInEndpoint`/`setEndpoint` in `SignInStore` only checks that the URL is syntactically valid HTTPS — it performs no verification that the host is actually a GitHub Enterprise instance beyond the same weak heuristics used elsewhere: [5](#0-4) [6](#0-5) 

The resulting UI presents a "Sign in to your GitHub Enterprise" popup that is silently pointed at the attacker's origin (`credentialHelperUrl: endpoint`), and any subsequent authentication (basic auth, PAT, or the enterprise OAuth device/browser flow) is then attempted against that attacker-controlled server. The other existing guards (`isDotCom`, `isGHE`, `isKnownThirdPartyHost`, hostname regex checks in `isGitHubHost`) are bypassed entirely because the `wwwauth[...]` branch returns early, before any of those stronger checks run: [7](#0-6) 

## Impact Explanation
A user cloning/fetching from a malicious or compromised remote (or hitting a malicious redirect/proxy during an existing operation) can be made to see a native-looking "Sign in to GitHub Enterprise" dialog whose real target is the attacker's server. Depending on which sign-in path the user follows (PAT/basic-auth entry vs. browser OAuth), this can result in the user typing a personal access token or completing an OAuth flow whose data is delivered to the attacker's endpoint instead of a real GitHub host — i.e. credential/token exfiltration via a spoofed but visually legitimate GitHub Desktop UI, and unauthorized account binding into Desktop's account store if the flow "succeeds." This matches the report's valid-impact class of "attacker controls...a git remote/proxy response" resulting in credential/token exfiltration or unauthorized OAuth/account binding.

## Likelihood Explanation
The trigger is a single crafted HTTP response header (`WWW-Authenticate: Basic realm="GitHub"`) returned by any Git host Desktop talks to — trivial for anyone who controls a remote URL the victim adds/clones/fetches, or who can intercept/redirect that traffic. No social engineering step beyond a normal clone/fetch is required beyond what the report's Valid Impact rules already allow ("a git remote/proxy response").

## Recommendation
Do not use the `wwwauth[...]` realm string alone as sufficient evidence to classify a host as GitHub/Enterprise for the purpose of *initiating a sign-in flow*. At minimum:
- Require a positive real-endpoint check (e.g. `isGitHubHost`'s `/meta` HEAD-request / `x-github-request-id` check, or a match against already-known accounts) before treating the header hint as authoritative.
- Or, when relying on the header hint, still surface the literal untrusted origin prominently in the sign-in UI and/or require explicit user confirmation that this is an Enterprise server they intend to authenticate against, rather than silently pre-populating and launching the flow.

## Proof of Concept
Not executable here (would require standing up a Git HTTP server), but the code path is fully deterministic from local source:
1. Attacker hosts a Git-over-HTTP remote (or a redirect/proxy in front of one) that responds to unauthenticated requests with `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds/clones/fetches this remote in GitHub Desktop.
3. Git invokes the credential helper trampoline; `cred` contains a `wwwauth[0]` entry with `realm="GitHub"`.
4. `getEndpointKind` returns `'enterprise'` at `app/src/lib/trampoline/trampoline-credential-helper.ts:158-160`.
5. No matching stored account exists for the attacker's endpoint, so `ui.promptForGitHubSignIn(endpoint)` is invoked with the attacker's URL (`trampoline-credential-helper.ts:118`).
6. `promptForGitHubSignIn` starts `beginEnterpriseSignIn` + `setSignInEndpoint(origin)` against the attacker's origin (`trampoline-ui-helper.ts:87-93`), producing a "Sign in to your GitHub Enterprise" dialog pointed at the attacker's server.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-130)
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
