### Title
Unauthenticated `WWW-Authenticate` header spoofing forces Desktop to treat an arbitrary git remote as a GitHub Enterprise host, triggering an attacker-hosted sign-in/PAT phishing flow - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The reported `Booster.approve()` bug is really about a privileged operation (granting third-party access to sensitive resources) being performed based on unvalidated/unrestricted input. The closest analog in GitHub Desktop is `getEndpointKind()` in the git-credential-helper trampoline, which grants a remote host "enterprise"/GitHub trust classification based solely on an attacker-controlled HTTP response header, with no server-identity verification, and then routes the user into a GitHub Enterprise sign-in flow scoped to that same attacker-controlled host.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper via the trampoline, forwarding any `WWW-Authenticate` header the remote server returned on its 401 response. `getEndpointKind()` classifies the endpoint using this attacker-suppliable content: [1](#0-0) 

Any HTTPS server the victim adds as a git remote (or is redirected/proxied to) can simply reply with `WWW-Authenticate: Basic realm="GitHub"` on the credential-fetch request. There is no cryptographic or DNS-based verification tying this header to an actual GitHub Enterprise install — it is a bare string match on attacker-controlled response data.

Once classified as `'enterprise'`, `getCredential()` checks whether an existing account matches the literal endpoint string; if not, it calls `ui.promptForGitHubSignIn(endpoint)`: [2](#0-1) 

`promptForGitHubSignIn` then starts an Enterprise sign-in flow scoped to that exact (attacker) origin and shows a "Sign in" dialog with `credentialHelperUrl` set to the attacker's endpoint: [3](#0-2) 

Because the dialog is triggered automatically as part of a normal fetch/clone/push against a remote the user added, the user has no reason to suspect this "GitHub Enterprise sign-in" prompt is not legitimate — the app itself decided the host "is GitHub" based on the spoofed header. If the user completes sign-in (PAT entry or OAuth) against that endpoint, the resulting account/token is bound with that endpoint as key and used as the git credential submitted back to the very same attacker-controlled host, and the account is persisted via `AccountsStore`.

### Impact Explanation
This allows an attacker who controls a git remote (or a proxy/MITM the user's client talks to over HTTPS) to make Desktop autonomously initiate a trust-elevated "GitHub Enterprise" sign-in workflow for an arbitrary domain, purely through response header content, without the user doing anything beyond a normal fetch/push. If the user proceeds with sign-in (PAT or OAuth-based), their token can be captured/associated with the attacker's endpoint, constituting credential/token exfiltration and unauthorized account binding — the exact class of harm the "no validation before privileged action" bug class describes (`approve`-without-restriction analog: granting elevated GitHub-trust classification without verifying the counterparty).

### Likelihood Explanation
Likelihood is moderate: it requires the user to add/use an attacker-controlled or attacker-proxied HTTPS git remote and for a credential prompt to occur (common in real workflows: private repos, expired tokens, generic Git hosting). The header spoof itself is trivial to add to any HTTP server response. It does still rely on the user actively completing a sign-in dialog rather than a fully silent exfiltration, keeping this out of the highest-severity tier, but it meets the "attacker controls a git remote/proxy response" criterion.

### Recommendation
Do not classify an endpoint as GitHub/Enterprise based solely on the unauthenticated `WWW-Authenticate` header content. At minimum: only use the header as a weak hint requiring corroboration (e.g., only trust it for hosts already associated with a known/previously verified account, or require a positive `isGitHubHost()` network probe result rather than a heuristic string match), and surface the actual literal hostname prominently in the sign-in dialog so users can recognize an unexpected domain before entering credentials.

### Proof of Concept
1. Stand up an HTTPS server at `https://evil.example.com/repo.git`.
2. Configure it to respond to Git's credential probe with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. In GitHub Desktop, add this URL as a remote / clone it, triggering a fetch that requires credentials.
4. Observe `getEndpointKind()` returns `'enterprise'` purely from the spoofed header [4](#0-3) , and since no account matches `evil.example.com`, Desktop opens a "Sign in to GitHub Enterprise" dialog scoped to `evil.example.com` via `promptForGitHubSignIn` [3](#0-2) .
5. If the user completes sign-in, the resulting credential is returned to the credential helper and submitted to the attacker's host, and the account entry is created/bound to `evil.example.com` as an "enterprise" endpoint in `AccountsStore`.

Note: I was unable to fully trace `beginEnterpriseSignIn`/`SignInStore` internals within the remaining budget (specifically whether OAuth vs. PAT entry is used for arbitrary non-`.com` endpoints and exact storage semantics), so the precise credential-capture mechanics of the sign-in flow itself should be verified in `app/src/lib/stores/sign-in-store.ts` and `app/src/ui/sign-in/sign-in.tsx` before treating the exploit chain as fully confirmed end-to-end.

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
