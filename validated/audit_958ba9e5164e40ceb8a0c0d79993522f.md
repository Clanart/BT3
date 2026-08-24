### Title
Attacker-Controlled `WWW-Authenticate` Header Triggers GitHub Sign-In and Sends Real GitHub Token to Arbitrary Host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The MUD report shows an attacker exploiting an under-specified trust boundary: a value that should be strictly scoped (a function selector namespace) is instead derived from attacker-influenceable input using a naive heuristic, letting the attacker steer classification/routing logic. The same broken-invariant pattern exists in Desktop's git credential helper: `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts` classifies a git remote as a trusted "GitHub" endpoint based on a `WWW-Authenticate` header string that is fully controlled by the remote server, and that classification directly gates whether a signed-in GitHub account's real OAuth/PAT token is handed back to git for that remote.

### Finding Description
When git needs credentials for a remote it invokes the credential helper trampoline with metadata captured from the HTTP exchange, including any `WWW-Authenticate` response headers (surfaced to the helper as `wwwauth[...]` entries). `getEndpointKind()` treats the mere presence of `realm="GitHub"` in that header as proof the remote is a genuine GitHub/GHE host: [1](#0-0) 

```
// When Git attempts to authenticate with a host it captures any
// WWW-Authenticate headers and forwards them to the credential helper. We
// use them as a happy-path to determine if the host is a GitHub host without
// having to resort to making a request ourselves.
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    }
    ...
```

This header is emitted by whatever server (or man-in-the-middle proxy) the user's git remote points to — it is not verified via any TLS pinning, real API call, or allow-list; the code comment itself explains this is a "happy-path" shortcut used specifically to *avoid* making a verifying request. Any arbitrary HTTP(S) endpoint the victim clones from or fetches through (a malicious `origin`, a malicious `insteadOf`/proxy rewrite, or a compromised/attacker-controlled submodule URL) can respond `WWW-Authenticate: Basic realm="GitHub"` and be classified `'enterprise'`.

That classification feeds directly into `getCredential()`: [2](#0-1) 

Because the endpoint is not `'generic'` and no existing account matches the fabricated `apiEndpoint`, the code calls `ui.promptForGitHubSignIn(endpoint)`, which opens the standard "Sign in to your GitHub Enterprise" dialog, telling the user "Git requesting credentials to access `<attacker-url>`": [3](#0-2) [4](#0-3) 

If the user completes this legitimate-looking sign-in flow (which performs a real OAuth/device-flow authentication against GitHub.com or the real GHE host they type in), the resulting `Account` — containing a real, valid GitHub access token — is merged into the credential and handed back to git: [5](#0-4) 

```
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
```

Git then performs Basic Auth to the *original* attacker-controlled URL using that username/token pair — sending the user's real GitHub credential to the attacker's server.

### Impact Explanation
This is credential/token exfiltration by an attacker who controls only a git remote/proxy response (no local access, no malware, no leaked credentials required). A malicious `git://`/`https://` remote (or MITM proxy on an `http://` remote, or a malicious fork used as a submodule URL) can trick GitHub Desktop into surfacing a trusted-looking sign-in prompt, and if the user completes it, silently exfiltrate a real GitHub PAT/OAuth token to the attacker's server via the Basic-Auth header of the ensuing git HTTP request. This mirrors the MUD bug's core lesson: using an attacker-supplied, unauthenticated value (there, a namespace/function-name string; here, a response header) to make a trust/authorization decision that should require verified provenance.

### Likelihood Explanation
Moderate. It requires the victim to add or use a remote pointing at an attacker-controlled/MITM'd host and to click through a sign-in prompt that looks legitimate (branded as "GitHub Enterprise" sign-in, showing the attacker's own URL as the target). No admin rights, no pre-existing compromise, and no unnatural steps beyond normal Desktop usage (adding a remote/cloning/fetching a submodule and authenticating when prompted) are needed. The `isGitHubHost()` network-verification fallback exists precisely because header-only detection is unreliable, but it is bypassed entirely once the `wwwauth[...]` heuristic matches.

### Recommendation
Do not trust the `WWW-Authenticate` realm string for classification. Either remove this heuristic entirely and always fall back to the network-verified `isGitHubHost()` check (which validates via `x-github-request-id` / `/meta` probing over HTTPS to the real hostname), or additionally require that the request host structurally matches a known GitHub/GHE naming pattern before honoring the header hint. At minimum, the sign-in prompt triggered via `promptForGitHubSignIn` should surface a stronger warning distinguishing "this host claims to be GitHub based on an unverified server response" before allowing OAuth credentials to be associated with it.

### Proof of Concept
1. Attacker hosts a git-over-HTTP(S) server (or sets up a transparent proxy in front of a plain `http://` remote) that responds to git's credential probe with `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote (or it is referenced as a submodule URL in a cloned repository) and performs a `fetch`/`clone`/`push`.
3. Git invokes the trampoline credential helper; `getEndpointKind()` sees the spoofed header and returns `'enterprise'`.
4. Because no account matches the fabricated `apiEndpoint`, Desktop shows the "Sign in to your GitHub Enterprise"/GitHub sign-in popup referencing the attacker's URL.
5. Victim completes sign-in (a real GitHub OAuth flow, so it "feels" legitimate) or reuses cached trust; the resulting `Account.token` is attached as the git credential.
6. Git sends this token as the Basic-Auth password to the attacker's original URL, exfiltrating it. [6](#0-5)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-178)
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

**File:** app/src/ui/sign-in/sign-in.tsx (L183-198)
```typescript
  private renderAuthenticationStep(state: IAuthenticationState) {
    const credentialHelperInfo =
      this.props.isCredentialHelperSignIn && this.props.credentialHelperUrl ? (
        <p>
          Git requesting credentials to access{' '}
          <Ref>{this.props.credentialHelperUrl}</Ref>.
        </p>
      ) : undefined

    return (
      <DialogContent>
        {credentialHelperInfo}
        {browserSignInInfoContent}
      </DialogContent>
    )
  }
```
