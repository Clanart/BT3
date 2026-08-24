## Title
Spoofed `WWW-Authenticate` header from an untrusted git remote silently triggers a GitHub-branded OAuth sign-in flow against an attacker-controlled host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

## Summary
The sherlock report's broken invariant is: an external, untrusted response is trusted to authorize a privileged, standing-approval action (moving the caller's full token balance) without validating that the responder is the intended, previously-approved counterpart. The Desktop analog is `getEndpointKind()` in the git credential-helper trampoline, which classifies an arbitrary HTTPS host as a legitimate "GitHub" endpoint purely from an unauthenticated `WWW-Authenticate` response header sent by that host's own git server, then automatically drives the user into a "Sign in to GitHub" flow pointed at that untrusted host.

## Finding Description
When Git needs credentials for any remote, GitHub Desktop's trampoline credential helper decides how to source them via `getEndpointKind()`: [1](#0-0) 

Crucially, if no first-party account already matches the remote's origin, the function inspects the `wwwauth[...]` header values that Git forwards from the remote server's HTTP response and, if any contains `realm="GitHub"`, unconditionally classifies the endpoint as `'enterprise'`:

```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
```

This header is fully attacker-controlled: it is emitted by the HTTP server the user is cloning/fetching/pushing to. A malicious git server (the "external report response" analog) simply has to answer any authentication challenge with `WWW-Authenticate: Basic realm="GitHub"`.

Once classified as `'enterprise'`, `getCredential()` requires a GitHub account for that origin and, finding none, calls: [2](#0-1) 

which invokes `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

This automatically calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` **using the attacker's own origin**, no user typing required, and shows the branded `PopupType.SignIn` dialog. If the user proceeds with "Sign in using your browser" (the only option offered by `AuthenticationForm`, see `app/src/ui/lib/authentication-form.tsx`), `authenticateWithBrowser()` opens the browser to: [4](#0-3) 

`getOAuthAuthorizationURL(endpoint, csrfToken)` builds this URL from the attacker's endpoint: [5](#0-4) 

So the OS browser is opened to `https://<attacker-host>/login/oauth/authorize?client_id=...`, a page fully controlled by the attacker, presented to the user immediately after a GitHub-Desktop-branded "Sign in to GitHub" prompt. If the flow is later completed (e.g., attacker replies via the `x-github-client://oauth?code=...&state=...` protocol handler that Desktop listens for), `resolveOAuthRequest()` exchanges the code: [6](#0-5) [7](#0-6) 

`requestOAuthToken()` POSTs the Desktop's bundled `client_id`/`client_secret` plus the code directly to the attacker's endpoint (`urlBase` = attacker's host), and then calls `fetchUser(endpoint, token)` against that same attacker endpoint — sending whatever "access token" the attacker returns straight back to it, and any legitimate credential the user typed into the attacker-controlled OAuth page never touches real github.com.

Existing guards do not stop this path: the origin-scoping in `findGitHubTrampolineAccount` (`app/src/lib/trampoline/find-account.ts`) only protects *already-registered* accounts; it does nothing to prevent classification of a *brand-new*, attacker-chosen host as "GitHub"/"enterprise" purely from a spoofable header, and the sign-in endpoint is set programmatically (`setSignInEndpoint(origin)`) rather than being something the user consciously typed and could sanity-check.

## Impact Explanation
An attacker who merely operates the git server the victim clones/fetches/pushes from (a normal, unprivileged position — e.g., a malicious "helpful mirror" URL, a compromised self-hosted git instance, or a URL shared as a "GitHub Enterprise" repo) can, with no other user interaction than the ordinary act of adding/fetching that remote, cause GitHub Desktop to:
- Present a first-party-branded "Sign in to GitHub" dialog, and
- Drive the OS browser to an attacker-controlled OAuth authorize page,

enabling credential phishing and, upon flow completion, exfiltration of any token exchanged during that flow (plus the app's OAuth `client_id`/`client_secret`, sent to the attacker's endpoint). This aligns with the eligible impact categories "credential/token exfiltration" and "unauthorized OAuth ... binding," triggered purely by an attacker-controlled git remote/proxy response.

## Likelihood Explanation
The trigger condition is trivial for the attacker to produce (any git-over-HTTPS server can send arbitrary `WWW-Authenticate` headers) and requires no privileged position — only that the victim add/use that remote, which is the normal, expected workflow of cloning/fetching a repository. The remaining step that requires user action is clicking "Sign in using your browser" on a dialog that already looks legitimate (GitHub Desktop's own sign-in UI), which is a plausible, low-friction action rather than an "unnatural user step."

## Recommendation
- Do not use the unauthenticated `WWW-Authenticate` realm string as sufficient evidence to classify an arbitrary host as a GitHub/Enterprise endpoint. At minimum, corroborate with an authenticated API probe (e.g., verify `/api/v3` or the enterprise version endpoint responds as expected) before offering the GitHub-branded sign-in flow.
- Before calling `setSignInEndpoint()`/`beginEnterpriseSignIn()` automatically, show the resolved host to the user for explicit confirmation instead of silently pre-filling and launching the flow from `promptForGitHubSignIn`.
- Consider maintaining an explicit allow-list of enterprise hosts the user has previously verified, rather than accepting on-the-fly classification per credential-helper invocation.

## Proof of Concept
1. Attacker stands up an HTTPS git server (e.g., using `git http-backend`) and configures it so that unauthenticated requests return `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim, in GitHub Desktop, adds/clones `https://attacker.example.com/whatever.git` (a normal, unprivileged clone action).
3. Git invokes the Desktop trampoline credential helper's `get` command; `getEndpointKind()` sees the spoofed `wwwauth[...]` header and returns `'enterprise'` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`).
4. No matching account exists for `attacker.example.com`, so `ui.promptForGitHubSignIn('https://attacker.example.com')` is invoked, which calls `dispatcher.setSignInEndpoint('https://attacker.example.com')` and shows the "Sign in to GitHub" popup (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-104`).
5. Victim clicks "Sign in using your browser"; Desktop opens `https://attacker.example.com/login/oauth/authorize?client_id=<real client id>&scope=...` in the OS browser (`app/src/lib/stores/sign-in-store.ts:284-303`, `app/src/lib/api.ts:2357-2368`) — a page fully controlled by the attacker who can present a convincing GitHub-lookalike login form.
6. If the attacker completes the custom-protocol callback with a `code`, Desktop's `resolveOAuthRequest` POSTs `client_id`/`client_secret`/`code` to `https://attacker.example.com/login/oauth/access_token` and then calls `fetchUser` against the same attacker host with whatever token is returned (`app/src/ui/dispatcher/dispatcher.ts:2269-2288`, `app/src/lib/api.ts:2370-2396`), completing the exfiltration/phishing chain.

Note: I could not fully trace the native custom-protocol handler (`x-github-client://oauth`) registration/validation code within the available index to confirm exactly how `resolveOAuthRequest` is invoked end-to-end; a Devin session with full repository access would be needed to verify that specific wiring in detail.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L109-125)
```typescript
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

**File:** app/src/lib/api.ts (L2370-2396)
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2269-2288)
```typescript
  /** Save the generic git credentials. */
  public async saveGenericGitCredentials(
    hostname: string,
    username: string,
    password: string
  ): Promise<void> {
    log.info(`storing generic credentials for '${hostname}' and '${username}'`)
    setGenericUsername(hostname, username)

    try {
      await setGenericPassword(hostname, username, password)
    } catch (e) {
      log.error(
        `Error saving generic git credentials: ${username}@${hostname}`,
        e
      )

      this.postError(e)
    }
  }
```
