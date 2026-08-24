## Title
GitHub-realm spoofing via `WWW-Authenticate` header tricks Desktop into binding real GitHub credentials to an attacker-controlled remote endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The `IOC`/`min_qty` bug is a case of the *enforcement check* being computed on a different (attacker-influenced) value than the one *actually used* by the engine, letting an attacker forge an input that satisfies the guard while still getting genuine execution. The GitHub Desktop analog is the git credential-helper trampoline's endpoint classification: `getEndpointKind` decides whether a remote counts as a trusted GitHub/Enterprise host by trusting a `WWW-Authenticate` header value that is returned by the **remote server itself** (an attacker-controlled git remote/proxy), rather than by validating the actual host. This "GitHub-ness" classification then drives whether Desktop prompts the user for a full GitHub OAuth/PAT sign-in and stores the resulting `Account` — with `endpoint` set to the attacker's arbitrary host — for later credential lookups.

### Finding Description
`getEndpointKind` in [1](#0-0)  determines the trust classification of a remote endpoint for the git credential helper. Besides checking known dotcom/GHE endpoints, it falls back to trusting attacker-suppliable data: [2](#0-1) 

Git forwards any `WWW-Authenticate` headers returned by the remote HTTP server to the credential helper as `wwwauth[...]` parameters. Because the code trusts `v.includes('realm="GitHub"')` to classify the endpoint as `'enterprise'`, any HTTP server the user's git operation talks to — including a malicious `git remote`/HTTP proxy the user has been lured into adding (e.g. via a crafted clone URL, a compromised mirror, or a MITM proxy) — can respond with:
```
WWW-Authenticate: Basic realm="GitHub"
```
and be classified as a GitHub host, even though it is not `github.com` or a real GHE instance (`isDotCom`/`isGHE` already failed, and `isGitHubHost` — an actual network probe — is never reached because the header short-circuits first).

This classification feeds into `getCredential` [3](#0-2) . Since the attacker host does not match any existing stored `Account.endpoint`, `ghCred` is empty, `endpointKind !== 'generic'` is true, and Desktop calls `ui.promptForGitHubSignIn(endpoint)` — showing a genuine GitHub/Enterprise sign-in dialog to the user, for an endpoint the attacker fully controls.

`promptForGitHubSignIn` [4](#0-3)  takes the raw attacker-supplied `endpoint`/`origin` (not `github.com`, since hostname check fails) and calls `dispatcher.setSignInEndpoint(origin)` before starting the "Enterprise" OAuth/PAT sign-in flow. On success it resolves an `Account` whose `endpoint` is the attacker's origin. Because `getGitHubCredential` (`findGitHubTrampolineAccount`, matching by `origin`) later looks up stored accounts purely by URL origin (`app/src/lib/trampoline/find-account.ts` lines 20-29), any *subsequent* git operation the user performs against that same attacker host will automatically supply the real GitHub username/token as HTTP Basic auth credentials — because the trampoline now believes that host is a trusted, previously-signed-in GitHub endpoint.

Unlike the original bug where a clamp (`ipx`) is correctly computed but the wrong variable (`px`) is used in a second calculation, here the "clamp"/trust-boundary check (`isDotCom`/`isGHE`/`isGitHubHost` network probe) exists but is bypassed by trusting a value the remote itself controls (`WWW-Authenticate` realm) before the safe check is ever reached.

### Impact Explanation
This allows an attacker who controls a git remote/HTTP proxy that the victim's Desktop client talks to (e.g., a malicious mirror added as a second remote, or a network proxy) to:
1. Trigger a legitimate-looking GitHub/Enterprise sign-in prompt for their own server.
2. Get the resulting `Account` (containing the user's real GitHub OAuth token or PAT) permanently bound to their attacker-controlled endpoint in Desktop's account store.
3. Have Desktop automatically re-send the victim's real GitHub credentials as HTTP Basic auth to that attacker endpoint on every future git network operation against it — a credential/token exfiltration path, matching the "unauthorized OAuth or account binding" / "credential exfiltration" categories explicitly called out as valid impact.

### Likelihood Explanation
The trigger condition — an HTTP git server returning a `WWW-Authenticate: Basic realm="GitHub"` header — is entirely under the control of any server the user's git client talks to for a credential-requiring operation (fetch/push/clone over HTTPS to a remote the attacker controls, or a MITM/rogue proxy). No local access, admin rights, or pre-existing malware is required; the standard git HTTP auth challenge/response mechanism is what carries the attacker's forged header into Desktop's trampoline. The remaining requirement — the user completing the sign-in dialog — is the same kind of "user clicks/consents through a standard-looking Desktop dialog" interaction accepted elsewhere in the valid-impact scope (e.g., "a link or deep link the user clicks").

### Recommendation
Do not classify a remote as a trusted GitHub/Enterprise host based solely on the `WWW-Authenticate` realm string returned by the remote itself. That header should, at most, be a *hint* to decide whether to perform the authoritative `isGitHubHost(endpoint)` network probe (which validates the actual `/api/v3` or dotcom identity) — never a substitute for it. Additionally, before calling `promptForGitHubSignIn`/`setSignInEndpoint` with an untrusted origin, require confirmation that the API endpoint was independently verified (e.g., successfully resolved via `isGitHubHost`) rather than self-declared by the remote server.

### Proof of Concept
1. Attacker stands up an HTTPS git server (e.g., a plain `git http-backend` or custom server) at `https://evil.example.com/repo.git`.
2. Attacker configures the server to answer unauthenticated requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim adds/uses this remote in GitHub Desktop (e.g., cloning or fetching it directly, or it is added as a secondary remote by some other means already in scope, such as a crafted clone URL).
4. Git invokes Desktop's credential helper trampoline; `parseCredential`/`cred.entries()` contains `wwwauth[0]=Basic realm="GitHub"`.
5. `getEndpointKind` returns `'enterprise'` for `evil.example.com` purely from that header [5](#0-4) .
6. `getCredential` finds no matching stored account and calls `ui.promptForGitHubSignIn('https://evil.example.com')`, which shows what looks like a standard "Sign in to GitHub Enterprise" dialog with the attacker's host prefilled, and calls `setSignInEndpoint('https://evil.example.com')` [6](#0-5) .
7. If the victim completes sign-in (OAuth/PAT), the resulting `Account.endpoint = 'https://evil.example.com'` is persisted.
8. On any future request to `evil.example.com`, `findGitHubTrampolineAccount` matches by origin and Desktop resends the victim's real GitHub token as the HTTP Basic auth password to the attacker's server.

Note: I could not fully trace whether an additional confirmation/warning dialog exists further upstream (e.g., in the `SignInResult`/PopupType.SignIn flow) that might mitigate step 6-7; this would need to be verified in a live Desktop session, since the index does not show the full sign-in dialog rendering logic for the enterprise/credential-helper path.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-135)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
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
}
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
