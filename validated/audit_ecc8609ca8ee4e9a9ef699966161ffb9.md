### Title
Unauthenticated `WWW-Authenticate` realm header from a malicious git remote is trusted to classify a host as GitHub Enterprise, triggering an unwanted OAuth/Enterprise sign-in binding - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`incentives.sol`'s `withdrawIncentives` trusted a caller-supplied `tokenAddress` without verifying it was actually the incentive token, letting an attacker "exchange" one asset identity for another it wasn't entitled to. The Desktop analog is `getEndpointKind()` in the git credential-helper trampoline: it trusts an attacker-controlled value (the `WWW-Authenticate` header text that git forwards from the remote server) as a "happy path" proof that a host is a GitHub/GitHub Enterprise endpoint, without ever independently verifying that identity claim, and lets that unverified classification drive credential and sign-in behavior.

### Finding Description
When git needs credentials for a remote it invokes Desktop's `credential.helper` trampoline. Desktop then decides how to treat the endpoint via `getEndpointKind`: [1](#0-0) 

The comment itself states the intent: *"When Git attempts to authenticate with a host it captures any WWW-Authenticate headers and forwards them to the credential helper. We use them as a happy-path to determine if the host is a GitHub host without having to resort to making a request ourselves."* The value of `wwwauth[]` comes straight from an HTTP response header returned by the remote server/proxy — i.e., it is fully attacker-controlled if the attacker controls the git remote or sits as a man-in-the-middle/misdirecting proxy. The code checks only `v.includes('realm="GitHub"')`, a trivial string match with no signature, no TLS-pinned identity check, and no fallback verification via `isGitHubHost()` once this string matches (that real check, which does make a network probe, is only reached if the header is absent or doesn't match).

This unverified "kind" then drives two different, higher-trust code paths in `getCredential`: [2](#0-1) 

If the spoofed header makes `endpointKind !== 'generic'` for a host that has no existing matching Account, Desktop calls `ui.promptForGitHubSignIn(endpoint)` where `endpoint` is the attacker's arbitrary URL: [3](#0-2) 

That helper binds the sign-in flow to the attacker's host and hostname is compared only against `'github.com'`; anything else routes into the Enterprise OAuth flow and calls `dispatcher.setSignInEndpoint(origin)` with the attacker-controlled origin — the invariant that the "endpoint being signed into" is actually a legitimate GitHub/GHE server is corrupted here, exactly analogous to `withdrawIncentives` never checking that `tokenAddress` really is the incentive token.

The same unverified classification also gates `storeCredential`/`eraseCredential`, which is the mirror problem: a malicious server can also poison the classification the *other* way (e.g. return a `realm="GitLab"`/`Gitea`/`Atlassian Bitbucket` string) to force `getEndpointKind` to `'generic'` for a host, and if the "generic" store/erase path is chosen instead of internal GitHub-account handling, Desktop's `setGenericCredential`/`deleteGenericCredential` operate on whatever `endpoint` string was derived from the request — bypassing the intended internal-only storage path for GitHub identities.

### Impact Explanation
An attacker who controls a git remote (or a proxy/MITM in the request path, which is squarely in-scope per the "attacker controls ... a git remote/proxy response" threat model) can spoof the `WWW-Authenticate` realm to:
- Force Desktop to initiate an "Enterprise" GitHub sign-in flow bound to the attacker's arbitrary host (`setSignInEndpoint(origin)`), which is unauthorized account/OAuth binding to a server the user never intended to authenticate against.
- Cause the "GitHub sign-in" popup UI to appear when the user is really talking to an untrusted host, increasing the chance credentials/OAuth flow completion get associated with the wrong endpoint.
- Manipulate whether credentials are handled via the internal GitHub-only path vs. the generic credential store, undermining the separation the code relies on to decide what data is safe to persist/read for a given host.

This matches the "unauthorized OAuth or account binding" and "attacker-controlled git remote/proxy response" categories explicitly listed as valid impact.

### Likelihood Explanation
The attacker only needs to run (or MITM/redirect to) a git server that responds to an authentication challenge with a crafted `WWW-Authenticate` header containing `realm="GitHub"` (or one of the other listed realms) — no user interaction beyond the normal "clone/fetch this remote" action is required, and no admin rights, local access, or prior malware are needed. This satisfies the "attacker controls ... a git remote/proxy response" precondition directly.

### Recommendation
Do not treat the `WWW-Authenticate` realm string as authoritative proof of host identity. Use it only as a weak hint that triggers the existing, stronger verification (`isGitHubHost(endpoint)` and/or a check against the account's real API endpoint) before deciding `endpointKind`, so no privileged behavior (sign-in prompts binding to `setSignInEndpoint`, or bypass of internal-account-only credential handling) is reachable purely from unauthenticated response header content controlled by the remote server.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or proxy) at `https://evil.example.com/repo.git` that the victim adds as a remote / clones.
2. On a git credential `get` request, the server (or a MITM in front of it) responds to the auth challenge with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential-helper trampoline stdin.
4. `getEndpointKind` matches `realm="GitHub"` and returns `'enterprise'` for `evil.example.com` without any further verification.
5. Since no `Account` exists for `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com')`, which (hostname ≠ `github.com`) calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint('https://evil.example.com')`, presenting the user a "Sign in to GitHub Enterprise" dialog bound to the attacker's server. [4](#0-3) [5](#0-4)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-135)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-178)
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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-104)
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
    }).catch(e => {
      log.error(`Could not prompt for GitHub sign in`, e)
      return undefined
    })
  }
```
