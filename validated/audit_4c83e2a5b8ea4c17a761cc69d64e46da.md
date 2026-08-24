## Title
`isGitHubHost()`'s unauthenticated substring heuristic lets an attacker-controlled git remote impersonate a GitHub Enterprise host and trigger a real sign-in/credential prompt - ([File: app/src/lib/api.ts])

### Summary
`isGitHubHost()` in `app/src/lib/api.ts` is used by the credential-helper trampoline (`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts`) to decide whether a git remote host should be treated as a trusted GitHub/GitHub Enterprise endpoint. Before doing any network verification, it short-circuits to `true` for any hostname that merely contains the literal substring `github.` preceded by a dot or the start of the string: [1](#0-0) 

This is the same class of bug as the audited Solidity report: a value that is *assumed* to reliably identify a specific, trusted target (there: a fixed `PositionManager` address assumed correct on every chain; here: a naive hostname pattern assumed to reliably identify "a GitHub host") is used without validating it against the actual, current context, causing the code to behave as if it's talking to the trusted entity when it may not be.

### Finding Description
`isGitHubHost()` is invoked from `getEndpointKind()`: [2](#0-1) 

Its logic: [3](#0-2) 

Any attacker-registered host that contains a `github.`-labelled segment (e.g. `github.attacker-domain.com`, or `auth.github.evil.net`) matches `/(^|\.)(github)\./` and is classified as a GitHub host **without** the live `x-github-request-id` probe that exists later in the same function for less-obvious hostnames: [4](#0-3) 

`getEndpointKind()` returns `'enterprise'` for that verdict. Back in `getCredential()`, once the endpoint kind is not `'generic'` and no existing account matches, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [5](#0-4) 

That helper opens the real Sign-in-to-Enterprise flow, using the attacker's hostname as the enterprise `origin`/endpoint: [6](#0-5) 

which drives `beginEnterpriseSignIn` / `setSignInEndpoint` and ultimately builds the OAuth authorize URL from `getHTMLURL(endpoint)` (or a Basic Auth prompt) pointed at the attacker's own domain: [7](#0-6) 

Nothing in this path re-verifies that the "enterprise" host is legitimate before surfacing a first-class Desktop "Sign in to GitHub Enterprise" UI for it.

### Impact Explanation
An attacker who controls a git remote URL (e.g. embedded in a repository the victim clones, a PR head clone URL, or a submodule URL) using a hostname such as `github.<attacker-domain>` can cause Desktop's credential-helper trampoline, while fetching/pushing to that remote, to classify the host as a GitHub Enterprise instance and pop the built-in "Sign in" dialog for it. A user who trusts this Desktop-native prompt may submit credentials or complete an OAuth authorization flow whose authorize/token endpoints resolve to the attacker's own server (since `getHTMLURL`/`getEnterpriseAPIURL` derive URLs straight from the attacker-supplied hostname), resulting in credential/token exfiltration to an attacker-controlled host. This matches the report's impact class of "silently trusting an unvalidated address/host," just moved from a fixed contract address to a fixed hostname pattern.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to add/clone/fetch from a remote whose host contains a `github.`-prefixed label (trivial for an attacker to register) and then to interact with the resulting sign-in prompt. This is lower likelihood than a fully silent exploit because it still needs user interaction with a credential/sign-in dialog, but no unnatural steps, local access, or pre-existing compromise is required — only a routine git remote add/clone/fetch, which is squarely within the described attacker model (attacker controls a git remote).

### Recommendation
- Remove or tighten the unauthenticated substring short-circuit in `isGitHubHost()` (`app/src/lib/api.ts`); do not classify a host as a GitHub/Enterprise host based purely on a `github.` substring match.
- Always require the live `/meta` probe (checking for the `x-github-request-id` response header) — or an explicit user-approved enterprise account registration — before treating an unknown host as trusted enterprise GitHub in `getEndpointKind()`.
- In `trampoline-credential-helper.ts`, only auto-trigger `promptForGitHubSignIn` for hosts that have been positively verified (not merely pattern-matched), and consider surfacing the actual target hostname/URL prominently in the sign-in dialog so users can recognize an unfamiliar/spoofed host before entering credentials.

### Proof of Concept
1. Attacker registers `github.attacker.example` (or any domain with a `github.`-prefixed label) and stands up an HTTPS git server / OAuth-lookalike endpoint there.
2. Attacker shares a repository/PR whose remote is `https://github.attacker.example/foo/bar.git`, or a submodule pointing at it.
3. Victim clones/fetches this remote in GitHub Desktop. Git invokes Desktop's credential helper trampoline for that host.
4. `getEndpointKind()` calls `isGitHubHost('https://github.attacker.example')`; the regex `/(^|\.)(github)\./` matches, so the function returns `true` immediately without probing the host, and `getEndpointKind` returns `'enterprise'`. [1](#0-0) 
5. Since no stored account matches this endpoint, `promptForGitHubSignIn('https://github.attacker.example/...')` is invoked, opening Desktop's native "Sign in to GitHub Enterprise" UI pointed at the attacker's host. [8](#0-7) 
6. The victim, trusting the Desktop-native dialog, completes sign-in (Basic Auth or OAuth) against the attacker's own server, exfiltrating credentials/tokens to the attacker.

Note: I was not able to fully trace `isGHE`/`isDotCom`/`isKnownThirdPartyHost` definitions (grep did not resolve their exact locations in the indexed slice), so I cannot rule out that some upstream guard filters known false positives in `isKnownThirdPartyHost` before the `github.` regex runs; however, the regex check itself remains a substring match with no ownership/DNS verification of the `github.`-labelled host, which is the core weakness described above.

### Citations

**File:** app/src/lib/api.ts (L2357-2368)
```typescript
export function getOAuthAuthorizationURL(
  endpoint: string,
  state: string
): string {
  const urlBase = getHTMLURL(endpoint)
  const scope = encodeURIComponent(oauthScopes.join(' '))

  return new window.URL(
    `/login/oauth/authorize?client_id=${ClientID}&scope=${scope}&state=${state}`,
    urlBase
  ).toString()
}
```

**File:** app/src/lib/api.ts (L2435-2454)
```typescript
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

**File:** app/src/lib/api.ts (L2465-2491)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-179)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
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
