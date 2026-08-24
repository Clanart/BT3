## Confirming the analog

`promptForGitHubSignIn` confirms the impact path: when `getEndpointKind` misclassifies an attacker's server as `enterprise`, `dispatcher.beginEnterpriseSignIn(cb)` + `dispatcher.setSignInEndpoint(origin)` is invoked with the **attacker-supplied `origin`**, and the sign-in popup is shown with `credentialHelperUrl: endpoint` pointed at the attacker's host — i.e. Desktop starts an "Enterprise" OAuth/account-binding flow against a server the attacker fully controls, based solely on an unauthenticated response header. [1](#0-0) 

### Title
GitHub host classification for credential prompts trusts unauthenticated `WWW-Authenticate` realm from the remote, bypassing the real `isGitHubHost` check - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind`, used by Desktop's git credential-helper trampoline to decide how to handle a credential request, classifies a remote endpoint as GitHub-owned (`enterprise`) based purely on the literal string content of a `WWW-Authenticate` (`wwwauth[]`) header value returned by the remote server during the git auth handshake, without ever performing the actual `isGitHubHost(endpoint)` network verification that exists later in the same function for the fallback case. Because this header is attacker-controlled content from an arbitrary/malicious git remote, an attacker who operates (or MITMs, via a plain-HTTP or self-hosted remote) a git server can force Desktop to treat their own domain as a trusted GitHub Enterprise endpoint.

### Finding Description
`getEndpointKind` short-circuits classification using unauthenticated header content: [2](#0-1) 

The loop at lines 156–165 accepts any `wwwauth[...]` entry whose value contains `realm="GitHub"` as proof the host is `'enterprise'`, and only falls back to the legitimate network-verified `isGitHubHost(endpoint)` call when no such header is present. This mirrors the structural flaw in the Livepeer report: a privileged classification (`isTranscoder` in the original bug, "is a real GitHub host" here) is derived from a value the counterpart never actually validated for that purpose (delegated stake amount vs. self-bond in the original; a client-supplied/negotiated header string here), and downstream logic (`_handleVotesOverrides` subtracting tally vs. `getCredential`/`getEndpointKind` granting `enterprise` treatment) acts on that unverified classification as if it were authoritative.

`getCredential` then uses this classification to decide the credential flow: [3](#0-2) 

If no account matches the (attacker-controlled) endpoint and `endpointKind !== 'generic'`, Desktop calls `ui.promptForGitHubSignIn(endpoint)` with the attacker's own URL. That function starts the Enterprise sign-in dialog bound to that endpoint: [1](#0-0) 

### Impact Explanation
This satisfies "unauthorized OAuth or account binding" from the valid-impact list: the sign-in/account-binding flow that is supposed to be reserved for verified GitHub Enterprise servers gets initiated against an attacker-controlled host purely because that host echoed a crafted `WWW-Authenticate: realm="GitHub"` header — something any HTTP server the user points a remote at can trivially send. The `isGitHubHost` network check, which exists precisely to prevent this kind of spoofing, is bypassed whenever the header-based shortcut matches. This can result in the OAuth/enterprise sign-in dialog and subsequent account-binding logic operating against the attacker's server (e.g. `setSignInEndpoint(origin)`), which is a meaningful trust-boundary break even though it stops short of remote code execution.

### Likelihood Explanation
Reachable without any privileged access: the attacker only needs the victim to add/fetch/clone from a git remote they control (a common, unprompted normal workflow — no unnatural user steps beyond "add this remote and fetch"), and to respond to the anonymous credential-negotiation request with a crafted 401/`WWW-Authenticate` header. This is well within reach of a malicious or compromised git server/proxy, matching the report's threat model of "a git remote/proxy response" attacker.

### Recommendation
Do not treat the `wwwauth[]` realm string as authoritative for GitHub-host classification. Either remove the header-based shortcut entirely and always fall back to the verified `isGitHubHost(endpoint)` network check, or require that the header-based signal only be used as a hint that still must pass the same verification (mirroring the original fix's principle: only credit privileged behavior when the underlying authoritative property, here "is actually GitHub", is independently confirmed rather than inferred from untrusted input).

### Proof of Concept
1. Attacker stands up an HTTP git server at `https://evil.example`.
2. Victim adds `https://evil.example/foo.git` as a remote in Desktop and performs a fetch/clone.
3. Git attempts anonymous access; the attacker's server responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
4. Desktop's credential trampoline receives this via `wwwauth[]` in the credential-fill call; `getEndpointKind` (lines 156–165) matches `realm="GitHub"` and returns `'enterprise'` without ever calling `isGitHubHost('https://evil.example')`.
5. Since no stored account matches `evil.example`, `getCredential` (lines 109–125) calls `ui.promptForGitHubSignIn('https://evil.example/...')`, which invokes `dispatcher.beginEnterpriseSignIn` and `dispatcher.setSignInEndpoint(origin)` bound to the attacker's origin — initiating GitHub Enterprise sign-in/account-binding UI against a server that was never verified to be a real GitHub instance. [4](#0-3) [5](#0-4)

### Citations

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
