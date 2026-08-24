### Title
Attacker-controlled `WWW-Authenticate` header spoofs GitHub Enterprise classification, binding OAuth sign-in flow to an untrusted origin - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The original report's broken invariant is: an internal handler trusts an untrusted signal (the reward token equaling `UNDERLYING_TOKEN()`) to decide whether to silently re-enter a privileged state (re-staking) on the user's behalf, even though the user explicitly asked to exit. The Desktop analog is `getEndpointKind()` in the git credential-helper trampoline, which trusts an attacker-controlled `WWW-Authenticate` response header to decide whether a completely arbitrary remote host should be classified as `'enterprise'` (i.e., a trusted GitHub Enterprise instance), and then silently drives the user into a GitHub sign-in / OAuth binding flow scoped to that attacker's origin.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper trampoline, forwarding any `WWW-Authenticate` header the *remote server* returned as `wwwauth[...]` parameters: [1](#0-0) 

`getEndpointKind()` classifies the host by iterating over these attacker-suppliable header values: if any contains `realm="GitHub"`, the endpoint is unconditionally treated as `'enterprise'` — with no verification that the server is an actual GitHub Enterprise instance: [2](#0-1) 

That classification flows directly into `getCredential()`. If the classified endpoint is not `'generic'` and no existing account matches, Desktop calls `ui.promptForGitHubSignIn(endpoint)` using the attacker-controlled `endpoint`: [3](#0-2) 

`promptForGitHubSignIn()` then binds the sign-in/OAuth flow to that origin: for any non-`github.com` hostname it calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` using the attacker's `origin`, and shows a `PopupType.SignIn` dialog flagged as `isCredentialHelperSignIn: true`: [4](#0-3) 

The only thing standing between an arbitrary HTTP server and this "enterprise" classification is a header value fully controlled by that same server. This is the "restaking" moment of the original bug: the code path that is supposed to gate access to a trusted operation (binding an OAuth/enterprise sign-in flow) instead auto-triggers based on state the untrusted counterparty supplies, with no independent check (no TLS pinning, no GitHub Enterprise "meta" discovery confirmation, no user confirmation that this is a fully separate flow from what they intended).

### Impact Explanation
A malicious git server (or a man-in-the-middle/reverse proxy sitting in front of any HTTPS git remote the user fetches/pushes/clones) can respond to Git's authentication probe with a crafted `WWW-Authenticate: realm="GitHub"` header. This causes Desktop to:
- Misclassify the attacker's arbitrary domain as a legitimate "GitHub Enterprise" endpoint (`getEndpointKind` → `'enterprise'`), and
- Automatically kick off `beginEnterpriseSignIn` + `setSignInEndpoint(origin)` bound to that attacker origin, surfacing a GitHub-branded sign-in dialog (`isCredentialHelperSignIn: true`) to the user in the middle of an otherwise ordinary git operation.

This matches "unauthorized OAuth or account binding" in the valid-impact list: the app is tricked into associating/soliciting enterprise-account sign-in state to an origin the user never intended to trust as a GitHub host, purely as a side effect of interacting with a repository/remote the attacker controls.

### Likelihood Explanation
The trigger requires nothing beyond what an attacker who controls (or proxies) a git remote already has: the ability to shape HTTP response headers during Git's normal credential-negotiation handshake, which happens automatically whenever Desktop performs a fetch/pull/push/clone against that remote. No local access, no prior compromise, and no unnatural user steps are required — simply adding/using a remote pointing at the attacker's server is enough to reach this code path.

### Recommendation
Do not trust the `WWW-Authenticate` realm string alone to classify a host as GitHub Enterprise. At minimum, corroborate it with an authenticated GitHub-specific signal (e.g., a successful call to the GHE metadata/discovery endpoint over the same TLS session, or an explicit user confirmation step before binding a sign-in flow to a previously-unknown origin), and clearly disclose to the user which origin the "GitHub sign-in" popup is actually authenticating against before any OAuth/token binding occurs.

### Proof of Concept
1. Host an HTTPS git server (or MITM proxy) at `https://evil.example.com/attacker/repo.git`.
2. Configure it so that on an authenticated git-http-backend request it returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. In GitHub Desktop, add this URL as a remote to any local repo and perform a `fetch`/`push`.
4. Git spawns the credential helper trampoline; `command.stdin` includes `wwwauth[0]=Basic realm="GitHub"`.
5. `getEndpointKind()` returns `'enterprise'` for `evil.example.com` purely from that header value.
6. `getCredential()` finds no existing account for that endpoint and calls `ui.promptForGitHubSignIn('https://evil.example.com/...')`.
7. `promptForGitHubSignIn` calls `dispatcher.beginEnterpriseSignIn()` and `dispatcher.setSignInEndpoint('https://evil.example.com')`, surfacing a GitHub Enterprise sign-in dialog bound to the attacker's origin.

Note: I was not able to fully trace the downstream `beginEnterpriseSignIn`/`sign-in-store.ts` OAuth exchange logic (e.g., whether it fetches a real GHE `/meta` endpoint before proceeding, which could partially mitigate this) within the available search budget — this would need to be confirmed in `app/src/lib/stores/sign-in-store.ts` to determine whether a genuine token exchange/credential capture against the attacker origin is reachable, or whether the flow fails at a later GHE-specific verification step.

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
