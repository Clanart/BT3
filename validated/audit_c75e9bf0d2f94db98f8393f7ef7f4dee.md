### Title
Attacker-controlled `WWW-Authenticate` realm spoofing tricks the credential helper into treating an arbitrary git host as GitHub, triggering an unauthorized "Sign in to GitHub" flow bound to the attacker's endpoint - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
The external report's broken invariant is: a security-relevant privilege decision (fee discount eligibility) is derived from a caller-supplied value (`actualUser`) instead of from a value that is independently verified against the true origin of the call (`msg.sender`). The Desktop analog is `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, which classifies a git remote as `'enterprise'` (i.e. "this is a GitHub host") purely by inspecting a `WWW-Authenticate` HTTP header value that is fully controlled by the remote server the user is fetching/pushing to — with no verification that the host is actually GitHub-operated.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper trampoline, which parses a `cred` map that includes any `wwwauth[]` values captured from the server's HTTP response headers: [1](#0-0) 

```
const getEndpointKind = async (cred: Credential, store: Store) => {
  ...
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }
  ...
}
```

This is exactly the "unverified actualUser" pattern: the classification of the endpoint as GitHub-flavored (`'enterprise'`) is taken at face value from a header string the remote server chose to send, with no cross-check against the actual hostname, a known API endpoint list, or a certificate/identity check. Any git HTTP server — including one operated by an attacker that the user was tricked into adding as a remote, or one supplying a malicious proxy response — can emit `WWW-Authenticate: Basic realm="GitHub"` on a 401 response.

This classification then drives `getCredential()`: [2](#0-1) 

```
async function getCredential(cred: Credential, store: Store, token: string) {
  ...
  const endpointKind = await getEndpointKind(cred, store)
  ...
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    ...
    const account = await ui.promptForGitHubSignIn(endpoint)
    ...
  }
```

Because the endpoint is misclassified as GitHub-like, and no matching account exists for the attacker's endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which — for anything other than `github.com` — starts a full "GitHub Enterprise" sign-in flow scoped to the attacker's arbitrary origin: [3](#0-2) 

```
public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
  ...
  const { hostname, origin } = new URL(endpoint)
  if (hostname === 'github.com') {
    this.dispatcher.beginDotComSignIn(cb)
  } else {
    this.dispatcher.beginEnterpriseSignIn(cb)
    await this.dispatcher.setSignInEndpoint(origin)
  }
  ...
}
```

The user is presented with a Desktop-native "Sign in to GitHub" dialog, but the endpoint being authenticated against is the attacker's arbitrary host, not a genuine GitHub/GHE server. Nothing in this path validates that the origin is a real GitHub Enterprise instance beyond the spoofable header.

### Impact Explanation
This breaks the same trust boundary as the report: a claim about identity/eligibility ("I am GitHub" via `realm="GitHub"`) is accepted without verifying it comes from the real party it claims to represent (a genuine GitHub/GHE host), analogous to accepting `actualUser` without checking `msg.sender`. Consequences:
- The user can be socially engineered into completing an OAuth/PAT sign-in flow against an attacker-controlled endpoint that visually presents itself, via Desktop's own chrome, as a legitimate GitHub sign-in.
- If the user enters a personal access token (reused across services, a common practice) rather than completing OAuth via browser, that token is sent directly to the attacker's server as part of `fetchUser`/validation calls in `sign-in-store.ts`, resulting in credential exfiltration.
- Even in the OAuth case, `setEndpoint`/`beginEnterpriseSignIn` results in a new account entry keyed to an attacker-controlled endpoint (unauthorized account binding), which can be leveraged for further phishing (e.g., subsequent "generic git credential" prompts for that same host will now look like a trusted "signed-in" endpoint).

This satisfies the valid-impact criteria: it is triggered purely by a git remote/proxy response (a `WWW-Authenticate` header), requires no local/physical access, admin rights, or pre-existing malware, and results in unauthorized account binding / potential credential exfiltration.

### Likelihood Explanation
Any git server (or a MITM/malicious proxy sitting on a path the user's git client is configured to trust, or simply an attacker-run repository host advertised to a victim, e.g. via a spoofed "self-hosted GitHub Enterprise" remote) can trivially add a `WWW-Authenticate: Basic realm="GitHub"` header to a 401 response. This requires no special server capability — it's a single response header the attacker's own HTTP server controls. The only user action needed is attempting to clone/fetch/push a remote pointed at the attacker's server (e.g., after being socially engineered to add it, or via a malicious `.gitmodules`/redirected remote), which is a normal, expected Desktop workflow, not an "unnatural" step.

### Recommendation
Do not trust the `WWW-Authenticate` realm string alone to classify a host as GitHub/Enterprise. Instead:
- Require an independent verification step (e.g., query `/meta` or a known GitHub Enterprise API endpoint over HTTPS and check the response signature/format) before treating an unknown host as `'enterprise'`.
- At minimum, prominently display the actual hostname being authenticated against in the sign-in dialog and clearly distinguish "detected via server-provided realm" from a verified GitHub Enterprise instance, so users aren't misled into believing this is an official flow purely due to Desktop-styled chrome.
- Maintain an allow-list/verification mechanism (mirroring the report's suggested router whitelist) so that only endpoints previously verified or explicitly added by the user via "Add Enterprise account" are treated as GitHub-flavored; unsolicited realm claims from arbitrary remotes should fall back to the `'generic'` credential flow.

### Proof of Concept
1. Attacker stands up an HTTP git server at `https://evil.example.com/repo.git` that responds to unauthenticated requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote in an existing repository (or clones it, e.g. via a crafted `.gitmodules` entry or by following a malicious link/instructions) and performs a fetch/push in GitHub Desktop.
3. Git invokes Desktop's credential helper trampoline; `parseCredential` captures the `wwwauth[]` header value into the `cred` map.
4. `getEndpointKind()` [4](#0-3)  matches `realm="GitHub"` and returns `'enterprise'`.
5. `getCredential()` finds no existing account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
6. `promptForGitHubSignIn` starts the Enterprise sign-in flow with `origin = https://evil.example.com`, showing Desktop's standard "Sign in to GitHub" popup scoped to the attacker's host.
7. If the victim completes sign-in (OAuth or PAT), the resulting token exchange/validation request goes to the attacker's server, exfiltrating the token or binding an account to the attacker's endpoint.

Note: I was unable to inspect the full `sign-in.tsx` popup rendering (tool access ended before retrieval) to confirm exactly what hostname text is shown to the user during this specific `isCredentialHelperSignIn` flow; a background agent should verify whether the attacker's hostname is displayed clearly enough to blunt the phishing risk, which would affect (but not eliminate) the severity of this finding.

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
