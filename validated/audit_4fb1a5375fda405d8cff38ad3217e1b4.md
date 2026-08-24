### Title
Spoofed `WWW-Authenticate` header from an untrusted git remote misclassifies the host as GitHub Enterprise, triggering a GitHub sign-in flow against an attacker-controlled endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` in the git credential-helper trampoline decides whether a host being authenticated to is `github.com`, `enterprise`, or `generic`. For hosts that aren't `github.com`/`*.ghe.com` and don't already match a stored account, it falls back to inspecting the `wwwauth[...]` credential fields that Git forwards from the server's HTTP `WWW-Authenticate` response header: [1](#0-0) 

The check is a bare substring match (`v.includes('realm="GitHub"')`) on data that originates entirely from the remote HTTP server's response, i.e., attacker-controlled when the "remote" is a malicious or compromised git server/proxy the user is fetching/cloning from.

### Finding Description
This is structurally the same bug class as the Putty report: a security-relevant branch (`isCall`/`isExercised` there; `endpointKind` here) is gated by a condition that can be satisfied by data the wrong party controls, causing the "wrong" code path to execute. In Putty, the strike/fee deduction fired on the untrusted/incorrect branch (expiry) instead of the intended one (exercise). Here, the "is this a GitHub host" classification fires on attacker-suppliable HTTP header content instead of any cryptographic or first-party-verified signal.

Any HTTPS git server can respond with `WWW-Authenticate: Basic realm="GitHub"` (or similar) to a credential request. Git captures this and passes it to the configured credential helper as a `wwwauth[N]` field, which Desktop's trampoline receives in `cred.entries()`. Once `getEndpointKind` returns `'enterprise'` for this untrusted host, `getCredential()` proceeds: [2](#0-1) 

Because no account exists for this new, attacker-controlled `apiEndpoint`, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which — since the hostname isn't `github.com` — calls `beginEnterpriseSignIn` and `setSignInEndpoint(origin)` using the attacker's origin: [3](#0-2) 

This causes GitHub Desktop to present its native "Sign in to GitHub Enterprise" dialog while silently pointing the enterprise sign-in flow (basic-auth or OAuth-style, depending on configuration) at the attacker's endpoint, without any indication to the user that the "enterprise" classification came from a spoofable HTTP header rather than a verified GitHub Enterprise install.

### Impact Explanation
If the user completes the resulting sign-in flow believing they're authenticating to a legitimate GitHub Enterprise instance (the dialog UI gives no indication otherwise, since `credentialHelperUrl`/origin is attacker chosen), their username/password or OAuth token exchange is sent directly to the attacker's server, resulting in credential/token exfiltration. This satisfies the "unauthorized OAuth or account binding" / "credential exfiltration" impact category: the trigger is a remote/proxy HTTP response the attacker controls, not any local access, malware, or leaked credential.

### Likelihood Explanation
The attacker only needs the victim to fetch/clone from (or otherwise perform a git HTTP operation against) a server they control or a MITM'd/compromised proxy — no unusual user interaction beyond a normal add-remote/clone/fetch is required, and Desktop's own trampoline credential helper is invoked automatically as part of any git network operation. The existing guards (`isDotCom`, `isGHE`, matching an existing stored account) don't stop this because they are all checked and fail through to the header-based heuristic; that heuristic is the last fallback specifically meant to reduce false network round-trips (`isGitHubHost`) but ends up trusting attacker-supplied header text as ground truth.

### Recommendation
Do not treat the `wwwauth[...]` `realm="GitHub"` substring as sufficient to classify a host as `enterprise`. At minimum:
- Require this heuristic to be corroborated by an independent signal (e.g., the actual `isGitHubHost()` API probe, `x-github-request-id`, or TLS/cert pinning to a known GHES install), rather than shortcutting on the header alone.
- Surface the endpoint being signed into more prominently in the sign-in dialog and treat header-classified "enterprise" hosts distinctly (e.g., require explicit user confirmation of the hostname) from hosts already known to the user (stored accounts, previously validated GHES installs).

### Proof of Concept
1. Attacker stands up an HTTPS git server (or MITM proxy) at `https://evil.example.com/some-repo.git`.
2. Victim adds this as a remote / clones it in GitHub Desktop.
3. On `git fetch`/`clone`, when Git requests credentials over HTTP, the attacker's server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this via the credential-helper protocol; Desktop's trampoline receives it as a `wwwauth[0]=Basic realm="GitHub"` field in `cred` [4](#0-3) .
5. `getEndpointKind` returns `'enterprise'` for `evil.example.com`.
6. `getCredential` finds no matching account for that endpoint and calls `ui.promptForGitHubSignIn('https://evil.example.com')` [5](#0-4) .
7. `promptForGitHubSignIn` opens the enterprise sign-in flow bound to `https://evil.example.com` [6](#0-5) , and any credentials/tokens entered are sent to the attacker's server.

Note: I could not fully trace the downstream `beginEnterpriseSignIn`/`setSignInEndpoint` implementation in `dispatcher.ts` in this session to confirm exactly which authentication mechanism (basic auth vs. OAuth device flow) is used for a non-dotcom endpoint at this stage — this would need to be verified by a Devin session with full file access before treating this as fully confirmed end-to-end, though the header-trust issue in `getEndpointKind` itself is verified directly from the code shown above.

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
