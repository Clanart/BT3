### Title
Attacker-controlled `WWW-Authenticate` realm spoofs GitHub Enterprise sign-in, redirecting credential prompts to a malicious origin - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The Git credential-helper trampoline classifies a remote endpoint as `'enterprise'` (i.e., "this is a GitHub Enterprise host") solely based on an attacker-controllable HTTP response header (`WWW-Authenticate: realm="GitHub"`) returned during Git's HTTP auth handshake, without any cryptographic or allow-listed verification that the host is actually a GitHub/GHE instance. This misclassification is analogous to the reported `GameNFT._update()` bug: a state-determining check (`isSoulbound` / `getEndpointKind`) is evaluated using attacker-influenced input instead of verifying the real precondition (actual previous owner / actual GitHub host), and downstream logic (mint-burn gating / sign-in flow selection) acts incorrectly as a result.

### Finding Description
In `getEndpointKind()` [1](#0-0) , when none of the fast local checks (`isGist`, `isDotCom`, `isGHE`, existing stored account) match, the code inspects the `wwwauth[...]` credential fields — which are populated directly from the HTTP `WWW-Authenticate` response header sent by the remote Git server during an HTTPS credential challenge — and if any such header's value contains `realm="GitHub"`, the endpoint is classified as `'enterprise'`: [2](#0-1) 

This header is fully attacker-controlled: any git server, proxy, or man-in-the-middle-capable HTTPS endpoint the user adds as a remote (or that a cloned repo's `.git/config` points to) can return `WWW-Authenticate: Basic realm="GitHub"` and be classified as `'enterprise'` — a "GitHub Enterprise" host — even though the domain has no relationship to `isGHE()`'s allow-list logic.

Downstream, in `getCredential()`, once the endpoint is classified as anything other than `'generic'` and no existing account matches that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` then extracts the `origin` from the attacker-controlled endpoint and calls `dispatcher.beginEnterpriseSignIn(cb)` followed by `dispatcher.setSignInEndpoint(origin)`, presenting the user a "Sign in to GitHub" dialog (`PopupType.SignIn`) that is actually configured to authenticate against the attacker's origin: [4](#0-3) 

Because this UI is rendered by the same trusted Desktop "Sign in" popup the user sees for legitimate GitHub.com/GHE sign-ins, and is triggered automatically from a background git operation (fetch/pull/clone against an attacker-controlled remote), the user has no strong signal that the "GitHub Enterprise" sign-in prompt is actually pointed at an attacker-chosen origin. Unlike the `isGHE()` check (which validates against a real, structured GHE domain pattern) or `isDotCom()`, this fallback path trusts unauthenticated response data from the very server being connected to, to decide whether to initiate a GitHub-branded authentication flow against that same server.

### Impact Explanation
If the user completes the resulting sign-in flow (basic auth or OAuth depending on `beginEnterpriseSignIn` implementation), their GitHub credentials/token could be submitted to, or bound to an account entry pointing at, an attacker-controlled origin — i.e., unauthorized OAuth/account binding and potential credential exfiltration, matching the "Valid Impact" criteria (attacker controls a git remote/proxy response; result is unauthorized OAuth/account binding or credential exfiltration). Impact is rated Medium because it still requires the user to interact with (complete) the misleading sign-in prompt, but no additional local access or user-initiated unnatural action is needed beyond adding/using a remote that Desktop already treats as a first-class operation (fetch, pull, clone).

### Likelihood Explanation
Likelihood is High: any HTTPS git remote can trivially set a custom `WWW-Authenticate` response header to `realm="GitHub"` on a 401 response; this is a normal, low-effort server-side configuration and requires no compromise of the user's machine, no leaked credentials, and no social engineering beyond the user having already added/using a repository/remote pointing at the attacker's server (already within the accepted attacker model: "a git remote/proxy response").

### Recommendation
- Do not classify a host as `'enterprise'`/GitHub-affiliated based solely on the `WWW-Authenticate` realm string returned by that same untrusted host.
- Require verification via `isGHE()`'s domain allow-list, or perform an independent, out-of-band check (e.g., call `isGitHubHost(endpoint)` against a known GitHub API meta-verification, not the challenge header) before initiating a GitHub-branded sign-in flow.
- At minimum, before invoking `promptForGitHubSignIn`, surface a clear warning to the user showing the literal origin the app is about to authenticate to, differentiating it visually from genuine `github.com`/known GHE sign-ins.

### Proof of Concept
1. Attacker sets up an HTTPS git server (or reverse proxy) at `https://evil.example.com/victim/repo.git`.
2. The server is configured to respond to unauthenticated Git HTTP requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim adds this URL as a remote (or clones it) in GitHub Desktop and performs a fetch/pull.
4. Git invokes the credential helper trampoline; `command.stdin` includes `wwwauth[]=Basic realm="GitHub"` [5](#0-4) .
5. `getEndpointKind` returns `'enterprise'` for `evil.example.com`.
6. `getCredential` finds no existing account for that endpoint and calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
7. Desktop shows its standard "Sign in to GitHub" dialog while `dispatcher.setSignInEndpoint(origin)` points the flow at `https://evil.example.com`, and any credentials/token the user submits are sent to the attacker's origin, or an account record gets bound to the attacker's endpoint under the guise of an "Enterprise" GitHub account.

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
