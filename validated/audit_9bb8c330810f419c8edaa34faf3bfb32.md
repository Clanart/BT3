## Title
Spoofed `WWW-Authenticate: realm="GitHub"` header from a malicious remote tricks the credential helper into an unauthenticated GitHub Enterprise sign-in bound to an attacker-controlled host, leading to persistent token exfiltration - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The `getEndpointKind` heuristic in Desktop's git credential helper trusts the `WWW-Authenticate` HTTP header forwarded by Git verbatim: any host that responds to an HTTPS git request with a header containing `realm="GitHub"` is classified as `'enterprise'`, i.e. a real GitHub Enterprise endpoint, with no further verification (no TLS/cert pinning, no API probe). This is the same class of bug as the reported staking issue: a value fully controlled by an untrusted party (the reward pool self-crediting mechanism there; the HTTP response header here) is trusted and fed directly into a privileged state transition (crediting the pool / classifying the host as GitHub and starting an authenticated sign-in flow), and once that state exists it is silently reused afterward without re-validation.

### Finding Description
`getEndpointKind` is invoked for every `git credential get` request that reaches Desktop's trampoline credential helper. Git forwards any `WWW-Authenticate` response header it receives from the remote as `wwwauth[N]=...` parameters to the credential helper protocol: [1](#0-0) 

Because this comes straight from the HTTP response of the git remote (or an HTTP(S) proxy in the path), an attacker who controls the remote server or a MITM/pinned proxy the user is cloning/fetching from can simply add `WWW-Authenticate: Basic realm="GitHub"` to their 401 response for any arbitrary domain. `getEndpointKind` will then classify that endpoint as `'enterprise'` even though it never verified the host is actually GitHub: [2](#0-1) 

That classification flows into `getCredential`. Since the endpoint is not `'generic'` and there is no existing `Account` for it, Desktop automatically opens the GitHub sign-in dialog bound to the attacker's endpoint: [3](#0-2) 

`ui.promptForGitHubSignIn(endpoint)` starts an Enterprise sign-in flow against that exact attacker-controlled origin: [4](#0-3) 

If the user completes this flow (which looks like a normal "Git requesting credentials to access `<host>`" GitHub Enterprise prompt), the resulting `Account` — including its access token — is persisted in `AccountsStore` keyed to that attacker endpoint. From then on, every future credential request whose origin matches will be auto-filled with that account's token via `findGitHubTrampolineAccount` / `credWithAccount`, with no further prompt, sending the token as HTTP Basic credentials to the attacker's server on every subsequent fetch/push: [5](#0-4) [6](#0-5) 

This mirrors the original bug's broken invariant exactly: a piece of state that should only ever be derived from a trusted source (in the contract, the pool's own accounting; here, "is this endpoint really GitHub") is instead derived from attacker-supplied input and then silently reused/self-perpetuated (the residual reward reused later; here the bound `Account`/token reused on every future request) without re-validating the original trust decision.

### Impact Explanation
A malicious or compromised git remote/HTTP proxy can cause Desktop to treat an arbitrary attacker-controlled domain as a trusted GitHub Enterprise endpoint, prompting the user through what looks like a normal sign-in flow and persistently binding an account/token to that attacker endpoint. Once bound, Desktop will keep transmitting that token to the attacker's server for every subsequent operation on that remote, constituting credential/token exfiltration and unauthorized account binding without any further explicit user consent per-request.

### Likelihood Explanation
The attacker only needs to control the server the victim is fetching/cloning/pushing from (or a proxy in that path) and needs the victim to interact once with the resulting sign-in prompt — no local access, malware, or leaked credentials required. Since Desktop's own SSH/host-verification logic elsewhere in the same file shows the project is aware that untrusted network responses need scrutiny, the acknowledgment of the header-based happy path as a heuristic ("without having to resort to making a request ourselves") is a deliberate trade-off that widens this attack surface.

### Recommendation
Do not let an unauthenticated `WWW-Authenticate` header alone elevate an unknown host to `'enterprise'` status. At minimum:
- Require an actual API probe (`isGitHubHost(endpoint)`) to confirm the host before offering/auto-triggering a GitHub sign-in flow, rather than trusting the `realm="GitHub"` string.
- Surface the true origin prominently and require explicit, unambiguous confirmation before binding any new Account to an endpoint discovered this way.
- Consider not auto-binding tokens to endpoints solely inferred from response headers; require the endpoint to be added first through the normal, explicit "Add Enterprise account" UI flow.

### Proof of Concept
1. Attacker stands up an HTTPS git server (e.g., `git.evil.example`) serving a repository the victim will clone.
2. On any authenticated request (or a forced 401), the server responds with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim clones/fetches via GitHub Desktop; Git forwards the header to Desktop's credential helper as `wwwauth[0]`.
4. `getEndpointKind` (app/src/lib/trampoline/trampoline-credential-helper.ts:153-165) returns `'enterprise'` for `git.evil.example`.
5. `getCredential` triggers `ui.promptForGitHubSignIn('https://git.evil.example')`, showing a "Sign in to GitHub Enterprise" dialog with credential-helper messaging referencing that host.
6. Victim completes sign-in (browser-based OAuth against the attacker's own domain, or credentials the attacker phishes via that page); an `Account` bound to `git.evil.example` with a real token is stored.
7. On every subsequent fetch/push to `git.evil.example`, `findGitHubTrampolineAccount` auto-supplies that account's token as Basic Auth credentials to the attacker's server, with no further prompt.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-57)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)

async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
```

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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
