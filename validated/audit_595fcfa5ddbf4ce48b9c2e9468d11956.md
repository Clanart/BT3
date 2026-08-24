### Title
Attacker-Controlled `WWW-Authenticate` Header Redirects Enterprise Sign-In Flow to Malicious Host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's git-credential trampoline classifies an unknown remote host as a GitHub Enterprise ("`enterprise`") endpoint based solely on the `WWW-Authenticate` header value that the remote HTTP server returns during a Git operation. This classification, together with `promptForGitHubSignIn`, causes Desktop to automatically point the "Sign in" (Enterprise) flow's origin at whatever host issued the request — without ever confirming that host is an actual, previously-trusted GitHub/GHE endpoint. An attacker who controls a git-over-HTTPS remote can trigger this flow and get the user's Enterprise credentials sent to the attacker's own server.

### Finding Description
`getEndpointKind()` inspects the `wwwauth[...]` credential fields (which come directly from the HTTP response headers the remote server sent back to git) and trusts them at face value: [1](#0-0) 

If the header contains `realm="GitHub"`, the function returns `'enterprise'` for *any* host, with no verification that the host is actually `*.github.com`, a real GHE deployment, or previously known to the user: [2](#0-1) 

This classification feeds directly into `getCredential()`. If no existing account matches the endpoint (which will always be true for a brand-new attacker host), Desktop automatically shows a sign-in prompt for that endpoint: [3](#0-2) 

`promptForGitHubSignIn(endpoint)` then decides which sign-in flow to start based purely on the hostname string parsed out of the attacker-supplied URL — again, with no validation that this host is a legitimate/known GitHub host: [4](#0-3) 

For any non-`github.com` hostname, it calls `beginEnterpriseSignIn(cb)` and `setSignInEndpoint(origin)`, i.e. it configures the "Enterprise" sign-in flow to authenticate directly against the attacker's `origin`.

This mirrors the report's broken-invariant pattern: just as `channelOpenAck()` assumed the connection was `OPEN` without checking `ConnectionEnd.State`, `getEndpointKind()`/`promptForGitHubSignIn()` assume a remote is a legitimate GitHub/Enterprise host based on an unauthenticated, attacker-suppliable signal (the `WWW-Authenticate` realm string and the bare hostname) instead of confirming it against any trusted state (e.g., a previously configured Enterprise account, a certificate-pinned identity, or an explicit user-added Enterprise endpoint).

### Impact Explanation
If a victim adds or is directed to a malicious git-over-HTTPS remote (e.g., a manipulated clone URL, a submodule pointing at attacker infrastructure, or an HTTP/HTTPS MITM proxy sitting in front of a real remote), and that server responds to unauthenticated requests with `WWW-Authenticate: Basic realm="GitHub"`, GitHub Desktop will automatically pop up a "Sign in" dialog during an otherwise ordinary `fetch`/`pull`/`push`/`clone`. Because `setSignInEndpoint(origin)` points this dialog at the attacker's own origin, any credentials or OAuth-driven authentication the user completes are sent to attacker infrastructure. This can result in exfiltration of Enterprise credentials/tokens — a direct authentication-bypass/credential-theft impact with no local access, admin rights, or pre-existing compromise required.

### Likelihood Explanation
Triggering the vulnerable path only requires the attacker to control the HTTP responses for a git remote the victim already interacts with (a very natural attack surface: malicious forks/clones with rewritten remotes, malicious submodule URLs, or network-position/proxy attackers for HTTP(S) git traffic) and to add one HTTP response header. No unusual or unnatural user action is needed beyond a normal `fetch`/`push`/`clone`, which is the exact kind of unprivileged action GitHub Desktop performs constantly. The remaining step (the user completing the resulting sign-in prompt) is a standard, expected part of the app's UX flow, not a contrived social-engineering step.

### Recommendation
Do not classify or trust a remote host as `enterprise`/GitHub based on unauthenticated signals it controls (the `WWW-Authenticate` realm string, or a bare hostname regex). Before invoking `beginEnterpriseSignIn`/`setSignInEndpoint`, confirm the target endpoint against a positively-verified source of trust — e.g., only allow the "GitHub" `wwwauth` fast path when the endpoint is already a known/allow-listed Enterprise account endpoint, or perform the existing `isGitHubHost()` network verification (checking for genuine `x-github-request-id` responses over a validated TLS connection) *before* auto-launching any credential-collecting sign-in UI, and clearly warn the user when a previously-unknown host is about to receive freshly-entered Enterprise credentials.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or a MITM proxy in front of any HTTP remote) at `https://evil.example.com/victim/repo.git`.
2. The server is configured so that any unauthenticated `git-upload-pack`/`git-receive-pack` request receives `401 Unauthorized` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim adds this URL as a remote (e.g., clones it directly, or it is substituted as a submodule/fork remote) and performs a normal `git fetch` from within GitHub Desktop.
4. Git invokes Desktop's credential-helper trampoline, forwarding the captured `wwwauth[]=Basic realm="GitHub"` header as part of the credential request.
5. `getEndpointKind()` returns `'enterprise'`; since no account exists for `evil.example.com`, `getCredential()` calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
6. `promptForGitHubSignIn` sees `hostname !== 'github.com'`, calls `beginEnterpriseSignIn(cb)` and `setSignInEndpoint('https://evil.example.com')`, then shows the Sign In dialog.
7. Victim, believing this is a legitimate prompt to authenticate to their own git host, enters Enterprise credentials, which are submitted to `evil.example.com`, giving the attacker the victim's credentials/token. [3](#0-2) [4](#0-3)

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-151)
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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-103)
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
```
