### Title
Malicious Git Remote/Proxy Can Spoof `WWW-Authenticate` Realm to Trigger Real GitHub Sign-In and Exfiltrate the User's Token as Basic-Auth Credentials to an Untrusted Host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The bug-class in the external report is "an authorization-relevant identifier is taken from attacker-influenced data instead of being verified against the actual/trusted actor, and that identity confusion drives a privileged action." The closest reachable analog in GitHub Desktop is in the trampoline Git credential helper: `getEndpointKind()` classifies a remote host as `'enterprise'` (a GitHub-like host) purely based on the content of a `WWW-Authenticate` header returned by the remote server, and `getCredential()` then uses that unverified classification to decide whether to invoke the real GitHub sign-in flow and hand the resulting GitHub account credentials back to Git for that (attacker-controlled) host.

### Finding Description
When Git performs HTTPS authentication it forwards `WWW-Authenticate` response headers to the credential helper as `wwwauth[...]` fields. `getEndpointKind()` uses this attacker/server-controlled content as a "happy path" signal: [1](#0-0) 

If the header contains `realm="GitHub"`, the host is classified `'enterprise'` even though it never passed through `isGitHubHost()`'s actual API probe. This classification is then used in `getCredential()`: [2](#0-1) 

Because there is no existing account bound to this arbitrary/untrusted endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which — for any non-`github.com` hostname — starts a *real* GitHub Enterprise sign-in flow bound to the attacker's `origin`: [3](#0-2) 

If the user completes this sign-in (believing it is a normal GitHub authentication prompt, since the UI is the standard sign-in dialog), the resulting `Account` — carrying the user's real GitHub OAuth token — is merged onto the credential response via `credWithAccount()`: [4](#0-3) 

That credential (username/token) is then handed back to Git as the answer for the `get` command, and Git sends it as Basic-Auth to the original untrusted remote/proxy — the same host that supplied the spoofed header in the first place. Nothing in this path re-verifies that the host git is authenticating against is the same trusted GitHub host the account was actually issued for; the only "verification" performed (`isGitHubHost`, `isDotCom`, `isGHE`) is intentionally skipped because the `wwwauth[]` heuristic short-circuits it.

### Impact Explanation
This is a credential/token exfiltration path: a repository the user has cloned/added, or any HTTPS git server the user fetches/pushes to (including a MITM proxy on an insecure network, or a malicious server the user is tricked into adding as a remote), can respond to Git's authentication probe with a crafted `WWW-Authenticate: realm="GitHub"` header. This does not require the host to actually be GitHub, own valid TLS certs for github.com, or any collusion from the real GitHub API. If the user, prompted by what looks like a normal "Sign in to GitHub Enterprise" dialog, completes authentication, their real GitHub personal token is transmitted to the attacker-controlled server via the git HTTPS Basic-Auth handshake. This matches the "attacker controls a git remote/proxy response... credential/token exfiltration" impact class.

### Likelihood Explanation
The heuristic is explicitly documented as a shortcut to avoid an API round trip ("we use them as a happy-path... without having to resort to making a request ourselves"), meaning it's intentionally reachable whenever Git receives any 401/403 with a crafted header from any HTTPS remote — this can be triggered simply by adding/cloning a repository pointing at an attacker-controlled server, with no local access, admin rights, or pre-existing compromise needed. The main mitigating factor is that it requires user interaction (completing the sign-in prompt), which lowers but does not eliminate likelihood, since the prompt is visually indistinguishable from a legitimate GHE sign-in request.

### Recommendation
Do not trust the `wwwauth[]` realm string alone to classify a host as GitHub/Enterprise. Require confirmation via `isGitHubHost()`'s actual API probe (or another cryptographic/API-based check) before routing to `promptForGitHubSignIn()`, and/or clearly display the actual untrusted origin/host being authenticated against in the sign-in dialog so users can detect a mismatch before their token is sent to that host.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or a MITM proxy for a plain HTTP remote) at `https://evil.example.com/foo.git`.
2. User adds/clones this URL in GitHub Desktop.
3. When Desktop's trampoline invokes Git and Git contacts `evil.example.com`, the attacker's server responds to the auth challenge with header `WWW-Authenticate: realm="GitHub"`.
4. `getEndpointKind()` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`) classifies the endpoint as `'enterprise'`.
5. `getCredential()` finds no existing account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
6. `promptForGitHubSignIn` (`trampoline-ui-helper.ts:87-93`) starts `beginEnterpriseSignIn` bound to `origin = https://evil.example.com`, showing the standard GitHub sign-in dialog.
7. User completes GitHub OAuth sign-in (against the real github.com OAuth flow, since `SignInStore` talks to `getEnterpriseAPIURL`/`getOAuthAuthorizationURL`, but the resulting `Account.token` is now associated with the credential response for `evil.example.com`).
8. `credWithAccount()` merges the resulting real token into the credential map, which is returned to Git and sent to `evil.example.com` as Basic-Auth — exfiltrating the user's GitHub token to the attacker's server.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-125)
```typescript
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
