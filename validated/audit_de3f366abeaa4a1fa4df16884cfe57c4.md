## Title
Attacker-controlled `WWW-Authenticate` realm spoofing tricks trampoline credential helper into treating an arbitrary Git host as GitHub Enterprise, redirecting sign-in/credential flow to the attacker's endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The reported Vader bug is fundamentally about an attacker being able to manipulate a value that the protocol treats as trustworthy (pool reserves) in order to redirect value flow. The closest analog found in GitHub Desktop's local code is `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, which classifies an arbitrary Git remote endpoint as `'enterprise'` (a first-party GitHub host) based on a `WWW-Authenticate` header value supplied by the remote server itself, with no independent verification. This classification then drives which authentication flow Desktop initiates against that untrusted host.

### Finding Description
When Git needs credentials for a remote it invokes Desktop's credential helper trampoline. `getEndpointKind` decides how to classify the endpoint: [1](#0-0) 

Specifically, it inspects `wwwauth[...]` entries — which are the raw `WWW-Authenticate` header(s) captured by Git from the HTTP response of the remote server — and if the value contains `realm="GitHub"`, it unconditionally classifies the host as `'enterprise'`: [2](#0-1) 

This header is fully attacker-controlled: any Git server (a malicious clone URL, a compromised/malicious HTTP git remote, or a MITM proxy on an HTTP git remote/proxy response) can respond to an unauthenticated request with `WWW-Authenticate: Basic realm="GitHub"` for any arbitrary hostname, even one with no relation to GitHub. Desktop treats this header as ground truth without contacting the real endpoint's `/meta` or otherwise validating that it is a genuine GitHub Enterprise install, unlike the fallback path which explicitly does perform a network probe (`isGitHubHost(endpoint)`).

Once classified as `'enterprise'`, `getCredential` (which calls `getEndpointKind`) treats the endpoint as a first-party GitHub host rather than a generic Git host: [3](#0-2) 

If no existing account matches that endpoint, Desktop prompts the user with `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

Note that `endpoint` here is derived directly from the attacker-controlled credential URL (`getCredentialUrl(cred)`), not from any trusted list. Since `hostname !== 'github.com'`, this calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` — i.e., Desktop's Enterprise sign-in flow is pointed at the attacker's arbitrary origin and a legitimate-looking "Sign in to GitHub Enterprise" dialog/OAuth flow is initiated against it.

### Impact Explanation
The broken invariant is: *"an endpoint is a trusted first-party GitHub/GHE host"* is derived from an attacker-supplied HTTP response header rather than from a verified/trusted source (known accounts, TLS-pinned domain list, or a genuine `isGitHubHost` network probe). This is directly analogous to the Vader bug where the trustworthiness of the exchange rate was derived from a manipulable, attacker-influenced source (pool reserves) instead of a manipulation-resistant oracle.

The consequence is that a user cloning/fetching/pushing to an attacker-controlled remote (e.g. a malicious clone URL shared with the victim, or a compromised generic Git host) can be steered into an "Enterprise sign-in" UI flow that is actually contacting the attacker's server. Depending on how the resulting enterprise-flow request is completed (basic auth vs OAuth device/browser flow), this can lead to the user's GitHub Enterprise credentials, OAuth authorization codes, or session material being sent to the attacker-controlled endpoint — i.e., credential/token exfiltration, matching the "credential/token exfiltration" and "unauthorized OAuth" categories called out as valid impact.

### Likelihood Explanation
The attacker primitive required is only control over the HTTP response of a Git remote the victim adds/clones/fetches/pushes to (a fully "attacker controls ... a git remote/proxy response" scenario per the task's valid-impact criteria) — no local access, no prior credential leak, and no admin rights are needed. Spoofing a `WWW-Authenticate: Basic realm="GitHub"` header on a 401 response is trivial for any HTTP server the attacker operates. This is a real network-observable behavior in Desktop's credential/auth flow, not a theoretical value.

### Recommendation
Do not trust the `WWW-Authenticate` realm string alone to classify a host as GitHub/GHE. Either:
- Always perform the manipulation-resistant `isGitHubHost(endpoint)` network probe (which independently queries the endpoint's real API) before elevating trust, removing the `wwwauth` shortcut entirely, or
- Require the `wwwauth`-based fast path to be corroborated by TLS certificate validation and/or a subsequent server-side `/meta`/well-known GitHub API check before routing the user into the enterprise sign-in flow, and clearly surface the actual origin being contacted to the user before any credentials are entered.

### Proof of Concept
1. Attacker stands up an HTTP(S) Git server at `https://evil.example.com/foo.git` (or MITM-proxies an existing generic remote).
2. Victim, in GitHub Desktop, adds/clones/fetches/pushes using this URL; Git performs an unauthenticated request and the server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Git invokes the trampoline credential helper `get` command with this header captured as a `wwwauth[...]` parameter.
4. `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:153-165`) sees `realm="GitHub"` and returns `'enterprise'` for `evil.example.com` without ever contacting the real GitHub API.
5. `getCredential` finds no existing account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com/foo.git')`.
6. `trampolineUIHelper.promptForGitHubSignIn` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-99`) sees hostname `!== 'github.com'`, calls `dispatcher.beginEnterpriseSignIn` and `setSignInEndpoint(origin)` with `origin = 'https://evil.example.com'`, launching the Enterprise sign-in UI pointed at the attacker's server.
7. The victim, believing they are authenticating a legitimate GitHub Enterprise instance, proceeds through the sign-in flow, which contacts the attacker-controlled origin — resulting in credential or OAuth-flow exposure to the attacker. [1](#0-0) [5](#0-4)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-135)
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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
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
