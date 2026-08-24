### Title
Spoofed `WWW-Authenticate` header can redirect the trusted "Sign in to GitHub Enterprise" flow to an attacker-controlled server, enabling credential/token exfiltration - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
`getEndpointKind()` trusts the `wwwauth[n]` credential fields that git forwards from the *server's own* HTTP `WWW-Authenticate` response header. Any git server (or a MITM/malicious proxy sitting in front of a legitimate-looking remote) fully controls this header value, yet Desktop uses it as a "happy path" to decide the host is a GitHub Enterprise instance, without any URL/host validation.

### Finding Description
`getEndpointKind` iterates the credential map entries and, if any `wwwauth[n]` value contains the literal substring `realm="GitHub"`, immediately classifies the endpoint as `'enterprise'` regardless of the actual hostname: [1](#0-0) 

These `wwwauth[n]` fields are populated by git from the literal `WWW-Authenticate` HTTP response header returned by whatever server the user's remote points to — fully attacker-controlled content, since the attacker operates (or is proxying) that server. No verification is done that the header actually came from `github.com`/a known GHE origin.

This classification feeds `getCredential()`, and because no stored account matches the attacker's arbitrary origin, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [2](#0-1) 

`promptForGitHubSignIn` then routes non-`github.com` hostnames into the Enterprise sign-in flow and explicitly points the sign-in target at the attacker-controlled `origin` derived from the same untrusted credential URL: [3](#0-2) 

The result is that Desktop's own native, trusted "Sign in to GitHub Enterprise" dialog — a UI users are conditioned to trust — is wired up to authenticate against the attacker's server (`beginEnterpriseSignIn` + `setSignInEndpoint(origin)`), purely because the attacker's server sent a crafted `WWW-Authenticate: ... realm="GitHub"` header on a 401 response during a normal fetch/clone/push. I was not able to trace the full internals of `beginEnterpriseSignIn`/`setSignInEndpoint` within the available index to confirm the exact wire format submitted to the attacker's origin (basic-auth POST vs. OAuth), so the precise transport of the exfiltrated secret (password vs. PAT) could not be fully verified with the tools available.

### Impact Explanation
An attacker who controls a git remote/proxy that the victim clones/fetches/pushes to can, purely via a spoofed response header, redirect GitHub Desktop's built-in GitHub Enterprise sign-in UI to their own server. If the user completes the resulting sign-in prompt (a normal action expected when authenticating to a repository), their real GitHub Enterprise credentials or personal access token would be submitted to the attacker's endpoint rather than the legitimate GHE server, resulting in credential/token exfiltration.

### Likelihood Explanation
Requires only that the attacker control (or intercept) the HTTP responses of a git remote the victim is using — no local access, no prior malware, no leaked credentials. Triggering it only needs a normal git operation (fetch/clone/push) against that remote returning a 401 with a crafted `WWW-Authenticate` header; this is well within the "attacker controls a git remote/proxy response" category described in scope.

### Recommendation
Do not trust the `wwwauth[n]` realm value alone to determine "GitHub-ness." At minimum, cross-check the reported realm against an actual network probe (`isGitHubHost`) or a known/allow-listed origin before elevating the credential UI to the trusted GitHub Enterprise sign-in flow, and never call `setSignInEndpoint` with an origin that hasn't been independently verified to be a real GitHub Enterprise instance.

### Proof of Concept
1. Host a git server (e.g. via `git http-backend` or any HTTP proxy) that, on any credential-required request, responds with `WWW-Authenticate: Basic realm="GitHub"`.
2. Have the victim add this server as a remote and run `git fetch`.
3. Git captures the header and passes `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper.
4. `getEndpointKind` returns `'enterprise'`; Desktop opens the native "Sign in to GitHub Enterprise" dialog pointed at the attacker's server (`setSignInEndpoint(origin)` where `origin` is the attacker's own host).
5. If the victim enters credentials, they are submitted to the attacker-controlled endpoint instead of a legitimate GitHub Enterprise server.

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
