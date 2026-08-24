### Title
Attacker-controlled `WWW-Authenticate` header spoofs "GitHub Enterprise" sign-in prompt, redirecting credentials to a malicious remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's Git credential-helper trampoline decides whether a remote host is "GitHub Enterprise" by trusting a `WWW-Authenticate` HTTP response header that Git forwards from the remote server itself. Because that header is fully attacker-controlled, a malicious or compromised HTTPS remote can force Desktop to treat itself as a GHE endpoint and pop up the "Sign in to GitHub Enterprise" dialog, pointed at the attacker's origin, during an ordinary `clone`/`fetch`/`push`. Any credentials/token the user then supplies are directed at the attacker's server rather than a real GitHub host.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper (`createCredentialHelperTrampolineHandler`) and forwards any `WWW-Authenticate` headers it captured from the server as `wwwauth[]` fields in the credential protocol payload [1](#0-0) .

`getEndpointKind()` uses this attacker-supplied header, with no host verification, as a "happy path" to classify the endpoint as GitHub Enterprise: [2](#0-1) 

If the endpoint is classified as non-`generic` (i.e. `enterprise`) and no existing account matches that host, `getCredential()` automatically triggers the sign-in flow: [3](#0-2) 

`promptForGitHubSignIn()` then starts the Enterprise sign-in flow pointed directly at the attacker's origin and shows the sign-in popup: [4](#0-3) 

This is the exact analog of the sudoswap invariant break: the code assumes a value (here, "this host is really GitHub Enterprise") is trustworthy because of where it *appears* to come from (a header relayed via Git), when in fact that value is entirely supplied by the untrusted counterparty (the remote/attacker), just as the sudoswap `_payProtocolFee` assumed funds were "at the pair" when they had actually already been routed to `assetRecipient`. No signature, TLS-pinned identity check, or actual API probe (`isGitHubHost`) is performed before trusting the header-derived classification — that safe check is used only as a *fallback* when no `wwwauth[]` header is present.

### Impact Explanation
A user cloning, fetching, or pushing to any attacker-controlled or compromised HTTPS git remote can be shown a Desktop-native "Sign in to GitHub Enterprise" dialog whose sign-in target (`origin`) is the attacker's server. If the user completes that authentication (basic auth or an OAuth-like flow the attacker's server mimics), their GitHub Enterprise credentials/token are sent to the attacker instead of a legitimate host. This matches the valid-impact category of "credential/token exfiltration" resulting from "a git remote/proxy response" under attacker control.

### Likelihood Explanation
The trigger requires nothing beyond the victim performing a normal Git operation (clone/fetch/push) against a repository whose remote is attacker-controlled or MITM'd — a scenario already assumed reachable per the task's threat model ("attacker controls a ... git remote/proxy response"). Setting a spoofed `WWW-Authenticate: Basic realm="GitHub"` header on a 401 response is trivial for any HTTP server operator, requiring no special privileges, malware, or leaked credentials.

### Recommendation
Do not use the `wwwauth[]` header alone to classify a host as GitHub Enterprise. Require corroboration via the existing safe check (`isGitHubHost()`, which actually probes the API) before triggering the Enterprise sign-in prompt, or at minimum warn the user distinctly when the "GitHub" classification came only from a header the remote itself supplied rather than a verified API response.

### Proof of Concept
1. Attacker stands up an HTTPS git server (e.g., via `git-http-backend` behind a reverse proxy) at `https://evil.example.com/repo.git`.
2. On any authenticated request, the proxy returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim runs `git clone https://evil.example.com/repo.git` inside GitHub Desktop (or Desktop performs a fetch/push against this remote).
4. Git relays the header to Desktop's credential helper via `wwwauth[0]=Basic realm="GitHub"`; `getEndpointKind()` returns `'enterprise'` [5](#0-4) .
5. Since no stored account matches `evil.example.com`, Desktop calls `ui.promptForGitHubSignIn('https://evil.example.com')` [6](#0-5) , which starts `beginEnterpriseSignIn` against that origin and shows the "Sign in to GitHub Enterprise" popup [7](#0-6) .
6. If the victim enters credentials, they are sent to the attacker's server.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-166)
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
