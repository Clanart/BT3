## Title
Attacker-controlled `WWW-Authenticate: Basic realm="GitHub"` header spoofs GitHub Enterprise host detection and redirects the "Sign in to GitHub Enterprise" flow to an attacker-controlled origin - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind` classifies a git remote purely from the *text* of the `WWW-Authenticate` header that the remote server returns during authentication, without verifying that the host is an actual GitHub/GHE instance. A server the user never trusted (e.g. one pasted into `CloneGenericRepository`) can force Desktop to treat it as `'enterprise'` and open the GitHub Enterprise sign‑in UI pointed at that server's own origin.

### Finding Description
When a `git clone`/`fetch` triggers Desktop's credential-helper trampoline, `getCredential` first checks first-party accounts, then calls `getEndpointKind`: [1](#0-0) 

This loop inspects the `wwwauth[N]` values that git forwards from the remote's `WWW-Authenticate` response header, and if the value contains the literal substring `realm="GitHub"`, the endpoint is unconditionally classified as `'enterprise'` — no network round-trip, TLS-identity check, or `x-github-enterprise-version` verification is performed for this branch (that verification only happens later, in the `isGitHubHost` fallback, which is skipped once the string match succeeds).

Back in `getCredential`, once `endpointKind !== 'generic'` and no existing account matches `apiEndpoint`, Desktop calls: [2](#0-1) 

`ui.promptForGitHubSignIn(endpoint)` then binds the sign-in UI to the attacker's own host: [3](#0-2) 

Because `hostname !== 'github.com'`, the code calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` where `origin` is derived directly from the untrusted credential URL — i.e., the attacker-controlled clone URL entered in `CloneGenericRepository`. This opens the "Sign in to GitHub Enterprise" dialog with the enterprise API base URL set to the attacker's server, purely because that server echoed the string `realm="GitHub"` in a 401 response.

### Impact Explanation
A user who pastes an attacker's git URL into `CloneGenericRepository` and then, when Desktop unexpectedly shows a "Sign in to GitHub Enterprise" popup, proceeds to authenticate (enters a Personal Access Token, or otherwise completes the flow presented for that endpoint) will have their sign-in flow directed at the attacker's chosen origin instead of a real GHE/GitHub instance, since `setSignInEndpoint(origin)` uses the attacker's host as the API base for that flow. This can result in the user's PAT or the outcome of an OAuth-style flow being sent to a host they never authorized, i.e. credential/unauthorized-OAuth-binding exfiltration as defined in the "Valid Impact" scope.

### Likelihood Explanation
Exploitation only requires the attacker to control the git server the victim clones from (satisfied by `CloneGenericRepository` accepting arbitrary URLs) and to reply to git's authentication attempt with a crafted `WWW-Authenticate: Basic realm="GitHub"` header — no special git server software is needed, any HTTP server can emit that header for a 401 response. This is a very low-cost primitive for an attacker. However, actual credential exfiltration still needs the user to notice and act on the unexpected sign-in prompt (e.g., enter a PAT), which requires some degree of user interaction/inattention rather than a fully silent chain — I could not fully verify from this repo whether the subsequent Enterprise sign-in flow (PAT entry vs. browser OAuth) transmits secrets to the spoofed `origin` immediately upon submission or performs any additional server identity validation before accepting credentials, since the sign-in store implementation was not reviewed in this pass.

### Recommendation
`getEndpointKind`'s `wwwauth[]` heuristic should not be trusted as sufficient proof of GitHub Enterprise identity for the purpose of *initiating an interactive sign-in flow bound to an arbitrary origin*. At minimum, the `'enterprise'` classification derived solely from header text should be corroborated with an authoritative check (e.g., `isGitHubHost`/`x-github-enterprise-version` verification, or TLS/cert pinning to known configured enterprise endpoints) before calling `setSignInEndpoint` with a value taken directly from the untrusted remote URL.

### Proof of Concept
1. Stand up any HTTP server (e.g. attacker.example) that responds to unauthenticated git HTTP requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, open "Clone Repository" → generic URL tab (`CloneGenericRepository`) and enter `https://attacker.example/foo.git`.
3. Desktop's git process attempts authentication; the trampoline credential helper's `get` command receives `wwwauth[0]=Basic realm="GitHub"`.
4. `getEndpointKind` matches `realm="GitHub"` and returns `'enterprise'` [4](#0-3) .
5. Since no account exists for `apiEndpoint`, Desktop calls `ui.promptForGitHubSignIn('https://attacker.example')`, which invokes `dispatcher.setSignInEndpoint('https://attacker.example')` and shows the "Sign in to GitHub Enterprise" dialog [5](#0-4) .
6. If the user completes sign-in believing this is a legitimate corporate GHE prompt, their credentials/token exchange is directed at `attacker.example`.

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
