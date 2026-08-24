### Title
Git remote-controlled `WWW-Authenticate` header spoofs GitHub host classification, misdirecting sign-in/credential flow - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The underlying bug class in the report is a "commit-before-challenge" failure: a security decision (the LogUp challenge) is derived from data that does not include everything the prover later controls (the Interaction field expressions), so the prover can freely choose the unobserved data after the decision is fixed and still pass verification. The Desktop analog is `getEndpointKind()` in the git credential-helper trampoline, which classifies a remote host as `'enterprise'` (i.e., "this is a trusted GitHub host, treat it accordingly") using a value the remote server fully controls — the `WWW-Authenticate` response header forwarded to Desktop by Git — instead of the value that is actually being verified (the real hostname of the remote).

### Finding Description
`getEndpointKind` decides how the trampoline credential helper should treat a remote endpoint: [1](#0-0) 

The relevant branch trusts the `wwwauth[...]` credential fields — which are populated directly from HTTP response headers that Git captures from the remote server and forwards to the credential helper via stdin — to decide the endpoint is a GitHub Enterprise host:
```
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
This check happens *before* the only "trusted" verification path — an authenticated HTTPS probe via `isGitHubHost(endpoint)` — is ever reached: [2](#0-1) 

The classification decision (`getEndpointKind`) is exactly the "challenge" in the analogy: it is supposed to be bound to the real identity of the remote (the hostname/certificate), but instead it is bound to an attacker-supplied header string, which the remote server can freely set to anything it wants, just like the malicious prover freely chose the LogUp field expressions after the (weak) challenge was fixed.

Once classified as `'enterprise'`, `getCredential()` takes the non-generic path: it checks for an existing Account matching the *attacker's own endpoint* (`apiEndpoint`, derived from the real request URL), and if none is found and this isn't a background task, it calls: [3](#0-2) 

`promptForGitHubSignIn(endpoint)` then drives the Enterprise OAuth/PAT sign-in flow, setting the sign-in endpoint to the attacker-controlled `origin`: [4](#0-3) 

Because the classification bypasses the only real host-identity check (`isGitHubHost`, an actual network probe of the endpoint) whenever a `wwwauth[...]=...realm="GitHub"` header is present, a malicious git remote/proxy can force Desktop into believing an arbitrary HTTPS host is a legitimate "GitHub Enterprise" instance and initiate the Enterprise sign-in dialog against that host.

### Impact Explanation
A user cloning/fetching from, or being redirected to, an attacker-controlled HTTPS remote (or an on-path proxy responding to Git's HTTP requests) can trigger Desktop's credential helper to receive a spoofed `WWW-Authenticate: realm="GitHub"` header. This flips the endpoint classification to `'enterprise'` without ever verifying the host is actually GitHub-compatible via `isGitHubHost`. The result is that Desktop presents the user with what looks like a normal "sign in to your GitHub Enterprise account" flow, but the sign-in endpoint (`origin`) is the attacker's own domain — i.e., Desktop is tricked into soliciting the user's GitHub Enterprise credentials/PAT/OAuth authorization for a server the user did not intend to authenticate to. This matches the "unauthorized OAuth/account binding" and "credential exfiltration" impact classes: the trust decision (host classification) is disconnected from the value it's supposed to protect (the real remote identity).

### Likelihood Explanation
The attacker only needs to control the HTTPS response for a git remote (a malicious/compromised git server, or a MITM/proxy the victim is configured to use for a repository) — no local access, no prior malware, and no unnatural user steps beyond a normal `git fetch`/`clone`/`push` against that remote, which Desktop performs routinely and automatically in the background (`getIsBackgroundTaskEnvironment` is the only gate, and it does not block this classification path for foreground operations). The header value that triggers the misclassification (`realm="GitHub"`) is trivial to set and is meant to be attacker-observable/controllable per the HTTP `WWW-Authenticate` spec.

### Recommendation
Do not use the self-reported `WWW-Authenticate` realm as a trust signal for classifying a remote as GitHub/GitHub Enterprise. Either remove this branch entirely and always fall back to the verified `isGitHubHost(endpoint)` check, or only use the header as a hint to *avoid* an extra network probe when the header explicitly indicates a *non*-GitHub product (as the `else if` branch already does for GitLab/Gitea/Bitbucket), never to *positively* assert a GitHub identity. Any code path that ultimately drives an OAuth/sign-in dialog or releases stored credentials must gate on cryptographically/network-verified host identity, not on data echoed back from the remote itself.

### Proof of Concept
1. Stand up a git-served-over-HTTP(S) endpoint (or a MITM proxy for a repository's remote) that, on an unauthenticated request, returns HTTP 401 with header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, add/clone this remote as a repository and perform a fetch/push that requires authentication.
3. Git captures the `WWW-Authenticate` header and forwards it to Desktop's credential helper trampoline as `wwwauth[]=Basic realm="GitHub"` on stdin (see `parseCredential` handling in `app/test/unit/git/credential-test.ts`), reaching `getEndpointKind()`.
4. Observe that `getEndpointKind` returns `'enterprise'` for this attacker-controlled endpoint without ever calling `isGitHubHost`, and that Desktop subsequently prompts an Enterprise GitHub sign-in dialog bound to the attacker's origin via `trampolineUIHelper.promptForGitHubSignIn(endpoint)`.

Note: I was not able to locate the implementations of `isGitHubHost`, `getAPIEndpoint`, and `getCredentialUrl` in the indexed code (only their usages in `api.ts`/`trampoline-credential-helper.ts` were found), so the exact verification logic bypassed by this header and the precise shape of `apiEndpoint`/account matching could not be fully confirmed from the index alone. A Devin session with full repository access would be needed to inspect `app/src/lib/api.ts` in full and confirm end-to-end exploitability (including whether any existing account could be silently matched instead of only triggering a new sign-in prompt).

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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-93)
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
```
