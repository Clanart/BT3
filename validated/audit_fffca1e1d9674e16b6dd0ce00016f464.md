## Title
Malicious git remote can spoof `WWW-Authenticate` header to trigger unauthorized GitHub Enterprise sign-in flow bound to attacker's server - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` in the credential-helper trampoline classifies a remote endpoint as GitHub `'enterprise'` based on an *unverified* `WWW-Authenticate` header value forwarded by the remote git server, **before** the authoritative `isGitHubHost()` API check ever runs. When Desktop has no stored account for that endpoint, this premature classification causes `getCredential()` to invoke `promptForGitHubSignIn(endpoint)`, which opens Desktop's real "Sign in to GitHub Enterprise" flow scoped to whatever origin the attacker's git server supplied — a value entirely outside the user's control.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper trampoline (`createCredentialHelperTrampolineHandler`) and forwards any `WWW-Authenticate` headers the *remote server* returned as `wwwauth[]` fields in the credential request [1](#0-0) .

`getEndpointKind()` treats a header containing `realm="GitHub"` as sufficient proof the host is GitHub Enterprise — this check runs before the real host verification (`isGitHubHost(endpoint)`) at the bottom of the function: [2](#0-1) 

Because this header is fully attacker-controlled server response content (not validated against any allowlist or DNS/TLS identity check), a malicious remote can force `endpointKind === 'enterprise'` for an arbitrary domain.

Back in `getCredential()`, once the endpoint is classified as non-generic and no existing account matches its origin, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` then drives the real Enterprise sign-in UI, binding it to the attacker-supplied origin: [4](#0-3) 

`this.dispatcher.setSignInEndpoint(origin)` is called with the attacker's origin, meaning any subsequent OAuth/PAT verification network calls made by the enterprise sign-in flow are directed at that attacker-controlled host, not a server the user ever configured.

The invariant the code assumes — "we only prompt GitHub sign-in for endpoints we've confirmed to be a genuine GitHub host" — is broken because the `wwwauth` "happy path" is placed ahead of the trustworthy `isGitHubHost()` check and uses attacker-supplied header content as the sole signal.

### Impact Explanation
This matches the report's bug class of "action allowed before the state that justifies it is actually established" — here, `endpointKind` is set to `'enterprise'` (justifying a trusted sign-in prompt) purely from unauthenticated attacker data, prior to the real verification step ever executing. A user fetching/cloning from a malicious remote can be shown Desktop's legitimate-looking GitHub Enterprise sign-in dialog pointed at the attacker's server. If the user enters a personal access token there (believing they're authenticating to their real GHE instance), that token is sent directly to the attacker's endpoint for verification — a credential exfiltration primitive. Even with OAuth-based sign-in, the resulting account is bound (`setSignInEndpoint(origin)`) to the attacker's arbitrary origin, an unauthorized account-binding outcome, and future git operations authenticated under that account can leak tokens to the attacker's host via `envForRemoteOperation`/credential fill flows [5](#0-4) .

### Likelihood Explanation
The trigger requires nothing beyond the user fetching, pulling, or cloning from a repository whose remote is (or later points to, e.g., via `git remote set-url` inside the repo, or a compromised/MITM'd server) attacker-controlled — a scenario squarely inside the "attacker controls a git remote/proxy response" threat model. No local access, admin rights, or unnatural steps beyond a normal git operation are needed to trigger the flawed classification; only the final acceptance in the sign-in dialog requires a user action, but that action is the same trusted action a legitimate GHE sign-in would require.

### Recommendation
Do not use `WWW-Authenticate` header content as a basis for classifying an endpoint as GitHub Enterprise. Either remove the `wwwauth` short-circuit entirely and always fall through to the authoritative `isGitHubHost(endpoint)` check, or require that check to be corroborated by an actual verified response from the target host before offering any GitHub sign-in prompt for previously-unknown endpoints. At minimum, surface the endpoint's origin prominently and unambiguously in the sign-in dialog and require explicit prior user opt-in for new enterprise hosts before initiating a sign-in derived from remote-triggered credential requests.

### Proof of Concept
1. Attacker sets up a malicious HTTPS git server (or MITMs a plain-HTTP/self-hosted remote) at `https://evil.example.com/repo.git`.
2. Victim adds this URL as a remote and performs `fetch`/`clone` in Desktop.
3. When Git requests credentials, the malicious server's HTTP response includes `WWW-Authenticate: Basic realm="GitHub"`.
4. Desktop's trampoline forwards this as `wwwauth[0]=Basic realm="GitHub"` to `getEndpointKind()`, which returns `'enterprise'` for `evil.example.com` without ever calling the real `isGitHubHost()` check [6](#0-5) .
5. Since no account is registered for `evil.example.com`, `getCredential()` calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
6. Desktop shows its native "Sign in to GitHub Enterprise" popup bound to `evil.example.com`; any PAT the user submits is sent to the attacker's server, and any resulting account is bound to that attacker-chosen origin.

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

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```
