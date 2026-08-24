This confirms the analog. When `promptForGitHubSignIn(endpoint)` is triggered with an attacker-controlled `endpoint` (not `github.com`), it calls `dispatcher.setSignInEndpoint(origin)` and `beginEnterpriseSignIn`, which drives the Enterprise OAuth sign-in flow (`getOAuthAuthorizationURL` / `requestOAuthToken` in `app/src/lib/api.ts`) against that attacker-controlled `origin` — including sending the app's `ClientID`/`ClientSecret` to it via `requestOAuthToken`. [1](#0-0) 

### Title
Spoofed `WWW-Authenticate` realm tricks Desktop into treating an arbitrary git host as GitHub Enterprise, triggering OAuth sign-in flow against the attacker's host - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
The bug-class from the report is "an assumption is hardcoded/trusted without re-validating it dynamically against the real, current source of truth, causing the system to act on stale/wrong classification." In GitHub Desktop, `getEndpointKind` classifies whether a remote Git host is `github.com`, `ghe.com`, `enterprise`, or `generic` before deciding how to handle credentials — and one of its heuristics blindly trusts the `WWW-Authenticate` HTTP header value forwarded by Git from the remote server, without any cryptographic or authoritative verification. [2](#0-1) 

### Finding Description
`getEndpointKind` first checks well-known first-party hosts (`isGist`, `isDotCom`, `isGHE`), then, for any other host, inspects the `wwwauth[...]` credential fields — data captured by Git from the server's HTTP `401 WWW-Authenticate` response header and forwarded verbatim to Desktop's credential helper — and classifies the host as `'enterprise'` if the header contains `realm="GitHub"`. [3](#0-2) 

This value is fully attacker-controlled: any git remote or HTTP proxy the user clones/fetches from can respond to an unauthenticated request with `WWW-Authenticate: Basic realm="GitHub"`, causing Desktop's credential helper to classify that arbitrary host as a GitHub Enterprise (`enterprise`) endpoint. There is no verification that the host actually is a GitHub Enterprise server (e.g. checking `x-github-enterprise-version`, hitting the real `/meta` API, or checking known GHE markers) at this stage — that verification (`isGitHubHost`) is only reached as a last-resort fallback if the header spoof isn't present, meaning the spoofed header intentionally short-circuits the more careful check. [4](#0-3) 

Once classified as non-`generic`, `getCredential` — invoked as the `get` implementation of Git's credential helper protocol during any authenticated fetch/push/clone — checks whether an account already exists for that endpoint; if not, it calls `ui.promptForGitHubSignIn(endpoint)`. [5](#0-4) 

`promptForGitHubSignIn` then drives the real GitHub Enterprise OAuth sign-in flow using the attacker's `origin` as the sign-in endpoint (`dispatcher.setSignInEndpoint(origin)` + `beginEnterpriseSignIn`), rather than any hardcoded/verified GitHub domain. [6](#0-5) 

That sign-in flow's OAuth code-exchange step, `requestOAuthToken`, sends the application's `ClientID`/`ClientSecret` and the OAuth authorization `code` directly to the (attacker-controlled) `endpoint` in a `POST` request. [7](#0-6) 

Similarly, `getOAuthAuthorizationURL` builds the authorization redirect URL by inserting `ClientID`/scopes into a URL rooted at the attacker's host, meaning the user is sent to the attacker's own server (masquerading as GHE) to "sign in," and any resulting authorization code or credentials handled during that flow go straight back to the attacker's server, not to a legitimate GitHub instance. [8](#0-7) 

### Impact Explanation
This lets an attacker who controls a cloned/fetched git remote (or a man-in-the-middle/malicious proxy for such a remote) leak Desktop's OAuth `ClientID`/`ClientSecret` to their own server and coerce the user into an "enterprise sign-in" UI flow pointed at their malicious host while Desktop displays it as a legitimate GitHub Enterprise sign-in — a credential/token-exfiltration and spoofing primitive triggered purely by server response content, with no local access or pre-existing malware required. [3](#0-2) 

### Likelihood Explanation
The trigger requires nothing more than the victim performing a normal `fetch`/`push`/`clone` against an attacker-hosted or MITM'd HTTP git remote that returns a crafted `WWW-Authenticate: realm="GitHub"` header on a 401 — a response Git itself will naturally elicit and forward as part of the standard credential-helper protocol, making this reachable without any unusual or unnatural user action. [9](#0-8) 

### Recommendation
Do not classify a host as `enterprise` (or otherwise privileged) solely from the unauthenticated `WWW-Authenticate` header text; treat it only as a weak hint that gates an additional authoritative check (e.g., always require `isGitHubHost`'s real HTTP probe / GHES version header confirmation, as already done at the bottom of `getEndpointKind`) before triggering any OAuth sign-in flow or exposing `ClientID`/`ClientSecret` to the endpoint. This mirrors the audited contract's fix pattern: never let an unverified, externally-supplied signal (here, an HTTP header from the remote itself) directly drive a trust decision without dynamic/authoritative confirmation. [10](#0-9) 

### Proof of Concept
1. Attacker hosts an HTTP(S) git remote (or sits as a MITM proxy for one) at `https://evil.example.com/repo.git`.
2. Victim adds/clones/fetches this remote in GitHub Desktop.
3. Git performs an authenticated request; the attacker's server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as a `wwwauth[...]` field to Desktop's credential helper (`createCredentialHelperTrampolineHandler` → `getCredential`). [11](#0-10) 
5. `getEndpointKind` matches `realm="GitHub"` and returns `'enterprise'` for `evil.example.com`, bypassing the authoritative `isGitHubHost` probe. [12](#0-11) 
6. Since no account exists for that origin, `ui.promptForGitHubSignIn('https://evil.example.com')` is invoked, showing Desktop's native "Sign in to GitHub Enterprise" dialog pointed at the attacker's host. [13](#0-12) 
7. If the user proceeds, `getOAuthAuthorizationURL`/`requestOAuthToken` send Desktop's `ClientID`/`ClientSecret` and any authorization code to `evil.example.com`, exfiltrating them to the attacker. [14](#0-13)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-178)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L220-248)
```typescript
export const createCredentialHelperTrampolineHandler: (
  store: AccountsStore
) => TrampolineCommandHandler = (store: Store) => async command => {
  const firstParameter = command.parameters.at(0)
  if (!firstParameter) {
    return undefined
  }

  const { trampolineToken: token } = command
  const input = parseCredential(command.stdin)

  if (__DEV__) {
    debug(
      `${firstParameter}\n${command.stdin
        .replaceAll(/^password=.*$/gm, 'password=***')
        .replaceAll(/^(.*)$/gm, '  $1')
        .trimEnd()}`
    )
  }

  try {
    if (firstParameter === 'get') {
      const cred = await getCredential(input, store, token)
      if (!cred) {
        const endpoint = `${getCredentialUrl(input)}`
        info(`could not find credential for ${endpoint}`)
        setHasRejectedCredentialsForEndpoint(token, endpoint)
      }
      return cred ? formatCredential(cred) : undefined
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

**File:** app/src/lib/api.ts (L2357-2395)
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
