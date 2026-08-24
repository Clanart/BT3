### Title
Spoofed `WWW-Authenticate` Header From Untrusted Git Remote Triggers GitHub Enterprise Sign-In Prompt To An Attacker-Controlled Host - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
The external report's broken invariant is: a low-cost, attacker-controlled signal (1 wei asset transfer) is trusted by a security-critical function (`executeBid`) without further verification, letting an outsider corrupt the risk/eligibility decision. The Desktop analog is structurally identical: `getEndpointKind()` in the git-credential trampoline classifies a remote endpoint as a genuine **GitHub Enterprise** host purely by trusting an HTTP `WWW-Authenticate` response header value (`realm="GitHub"`) that is fully attacker-controlled, and this classification directly drives whether Desktop pops the trusted, first-party "Sign in to GitHub" dialog pointed at that (attacker) origin.

### Finding Description
When Git needs credentials for an HTTP(S) remote, it invokes Desktop's credential helper trampoline, forwarding any `WWW-Authenticate` headers returned by the server as `wwwauth[N]` fields. [1](#0-0) 

`getEndpointKind()` treats a header containing `realm="GitHub"` as proof the endpoint is a trusted GitHub Enterprise host — a "happy path" used specifically *to avoid making a verifying network request*: [2](#0-1) 

This value is never validated against any real GitHub Enterprise metadata endpoint (`isGitHubHost()` is only reached as a fallback when the header trick does *not* match). Any git remote, submodule URL, or transparent HTTP proxy the user's clone/fetch/pull touches — none of which require any special privilege to control — can return a 401 response with `WWW-Authenticate: Basic realm="GitHub"` for an arbitrary hostname.

Once classified as `enterprise`/non-generic and no matching account exists, `getCredential()` calls into the UI layer: [3](#0-2) 

`promptForGitHubSignIn()` then wires up a first-party "Sign in to GitHub" flow bound to the attacker's `origin`, using Desktop's own trusted sign-in dialog: [4](#0-3) 

The corrupted value is the `endpointKind`/`origin` derived entirely from attacker-supplied header text; Desktop's own network-based verification (`isGitHubHost`) is bypassed because the header shortcut is checked first and returns early.

### Impact Explanation
An attacker who controls a git remote/proxy that the victim's Desktop client talks to (e.g., a malicious repository the user clones, a compromised/rogue submodule remote inside an otherwise legitimate repo, or a man-in-the-middle HTTP proxy on an insecure network) can cause Desktop to display its own trusted GitHub Enterprise sign-in UI pointed at the attacker's chosen host. Because the dialog is Desktop's genuine first-party sign-in flow (`beginEnterpriseSignIn` / `setSignInEndpoint`), a user who completes it will send their GitHub PAT or OAuth flow to the attacker's server, resulting in credential exfiltration and unauthorized account/OAuth binding to an attacker-controlled endpoint — matching the "unauthorized OAuth or account binding"/"credential exfiltration" impact classes.

### Likelihood Explanation
The attacker only needs a repository or remote the victim will fetch/clone/pull (including a submodule pointed at attacker infrastructure) and a trivial HTTP 401 response with a crafted header — no local access, no leaked credentials, no admin rights, and no unnatural extra user steps beyond the normal act of cloning/fetching a repository and responding to a sign-in prompt that Desktop itself initiates as part of the standard authentication UX. This is squarely within the "attacker controls a cloned/fetched repository ... or git remote/proxy response" valid-impact category.

### Recommendation
Do not use the client-supplied `WWW-Authenticate` realm string as sufficient proof of GitHub identity. Always require the network-based verification (`isGitHubHost()` / real GHE `/api/v3/meta`-style check) before offering the GitHub/GHE sign-in shortcut, and clearly surface the actual target hostname in the sign-in dialog so users can detect a mismatch, rather than implicitly trusting a spoofable header as a bypass for that verification.

### Proof of Concept
1. Attacker hosts (or MITMs) an HTTPS git server at `https://attacker.example` and configures it to respond `401 Unauthorized` with header `WWW-Authenticate: Basic realm="GitHub"` for any git-over-HTTP request.
2. Victim clones/fetches a repository whose remote (or a `.gitmodules` submodule entry) points at `https://attacker.example/...`.
3. Git forwards the `WWW-Authenticate` header to Desktop's credential trampoline as `wwwauth[0]=Basic realm="GitHub"`.
4. `getEndpointKind()` returns `'enterprise'` for `attacker.example` without any verification request. [5](#0-4) 
5. `getCredential()` finds no matching stored account and calls `ui.promptForGitHubSignIn('https://attacker.example')`. [6](#0-5) 
6. Desktop shows its trusted "Sign in to GitHub" popup bound to `attacker.example`; if the user proceeds, their credentials/OAuth token are sent to the attacker's server. [7](#0-6)

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
