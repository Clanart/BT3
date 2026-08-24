### Title
Spoofable `WWW-Authenticate` header lets an attacker-controlled git remote impersonate a GitHub Enterprise host and trigger a bound OAuth/token sign-in flow that discloses the resulting account credential to that remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The report's broken invariant is: a security-relevant "authority" decision is derived from a value the untrusted counterparty supplies, instead of from the object that should actually be trusted. In GitHub Desktop's git credential trampoline, `getEndpointKind` classifies an arbitrary remote host as a genuine GitHub Enterprise (`'enterprise'`) endpoint solely because the HTTP response for that host contained a `WWW-Authenticate` header with `realm="GitHub"` — a value fully controlled by whatever server (or MITM/proxy) the user's git operation is talking to.

### Finding Description
In `getEndpointKind`, after the safe checks (`isGist`, `isDotCom`, `isGHE`, which rely on locally known endpoints), the code falls back to trusting a header echoed from the remote server: [1](#0-0) 

Any git server (a cloned/fetched repository's remote, or a network path controlled by an attacker acting as that remote) can respond to Desktop's request with `WWW-Authenticate: realm="GitHub"` and be classified as `'enterprise'` even though it is not a real GitHub Enterprise instance. This classification feeds directly into `getCredential`: [2](#0-1) 

Because no local account matches this attacker's endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, passing the attacker-controlled `endpoint` string unmodified: [3](#0-2) 

`promptForGitHubSignIn` then binds the *legitimate* GitHub Enterprise sign-in machinery to that attacker origin: `dispatcher.beginEnterpriseSignIn(cb)` followed by `dispatcher.setSignInEndpoint(origin)`, where `origin` is parsed straight from the attacker's endpoint. If the user completes this sign-in dialog (which Desktop presents as an ordinary, trusted "Sign in to GitHub Enterprise" prompt, since the UI has no independent way to show that the host is not a real GHE server), the resulting `Account` — carrying a real access token — is merged into the credential (`credWithAccount`) and handed straight back to git, which sends it as the `Authorization`/Basic-auth header to the attacker's server. The account is also persisted by the accounts store keyed to the attacker's endpoint, so subsequent fetch/push operations against that same malicious host will silently and automatically resend the token in the future.

This mirrors the underlying flaw class in the seed report exactly: a caller/host is authorized based on the wrong (attacker-influenced) identity field rather than a value the application actually controls or has independently verified — `offerInfo.authority` (wrong party) vs. `stockInfo.authority` (correct party) in the contract; here, "any server sending `realm="GitHub"`" (attacker-controlled) vs. an endpoint Desktop has actually verified as GitHub-owned.

### Impact Explanation
A user who clones or fetches from an attacker-supplied repository URL (or whose git traffic is routed through an attacker-controlled proxy/remote) can be made to enter GitHub credentials/complete an OAuth or token sign-in flow that is silently bound to the attacker's host. The resulting token is (a) transmitted directly to the attacker's server as HTTP Basic auth, and (b) persisted as a stored account tied to that attacker endpoint, causing automatic, repeated credential disclosure on every future git operation against that remote. This is a credential/token exfiltration and unauthorized account-binding primitive, entirely attacker-triggerable via a git remote's own HTTP response — no local access, malware, or prior credential leak needed.

### Likelihood Explanation
Exploitation only requires the victim to run an ordinary git operation (clone/fetch/push) against a repository or remote the attacker controls (or a network path that can inject the `WWW-Authenticate` header), which is a completely realistic Desktop workflow (adding a malicious remote, cloning a malicious repo link, or a compromised/untrusted proxy). The only user action needed is completing the sign-in dialog that Desktop itself presents, which looks like Desktop's normal GitHub Enterprise authentication UX — no unnatural steps beyond normal app usage.

### Recommendation
Do not use a server-supplied `WWW-Authenticate` realm string as sufficient evidence that a host is a genuine GitHub/GitHub Enterprise instance. At minimum:
- Require a positive, verifiable signal (e.g., successful `isGitHubHost` API probe, or an existing known/pinned enterprise endpoint) before offering the `'enterprise'` sign-in flow, rather than trusting the header on the "happy path."
- Before binding `beginEnterpriseSignIn`/`setSignInEndpoint` to a new origin discovered this way, clearly warn the user that this origin has never been previously verified as a GitHub Enterprise server and let them confirm/reject it.
- Ensure any account created via this flow cannot be silently reused for future automatic credential injection until the host has been explicitly trusted by the user.

### Proof of Concept
1. Attacker stands up a plain HTTP(S) git server (or a MITM proxy in front of an arbitrary remote) that, on an unauthenticated git-over-HTTP request, replies `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote in GitHub Desktop and performs a fetch/clone/push.
3. Git invokes Desktop's credential helper trampoline; `getEndpointKind` sees the `wwwauth[...]` header and returns `'enterprise'`: [4](#0-3) 
4. Since no stored account matches the attacker's host, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` = the attacker's URL: [5](#0-4) 
5. `promptForGitHubSignIn` binds the enterprise sign-in flow to the attacker's origin and shows the standard sign-in popup: [6](#0-5) 
6. Victim completes sign-in (e.g., enters credentials or a PAT believing it's for a legitimate GHE server). The resulting `Account`'s token is merged into the credential and returned to git, which sends it to the attacker's server as Basic auth, and the account is stored, bound to the attacker's endpoint, for reuse on future operations.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-166)
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
