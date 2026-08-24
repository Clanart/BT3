## Title
Spoofable `WWW-Authenticate` header lets a malicious/MITM git server force Desktop's "Sign in to GitHub Enterprise" flow for a non-GitHub host, phishing GHE/GitHub credentials - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Sherlock report shows a "strategyId" parameter that is fully attacker/user-controlled being used to pick which security policy (`maxLTV`) applies, instead of the policy that is actually tied to the position's real collateral, letting the check be satisfied against the wrong object. The equivalent broken invariant in GitHub Desktop is in the git credential-helper trampoline: the *kind* of endpoint (`'github.com' | 'enterprise' | 'generic'`) — which decides whether Desktop treats a remote host as a trusted GitHub identity and launches the GitHub sign-in UI for it — is derived from a value that the remote server itself supplies (`WWW-Authenticate` response header), not from an authoritative, locally-controlled check of the endpoint's real identity.

### Finding Description
`getEndpointKind()` in [1](#0-0)  classifies a credential-helper request's endpoint. After failing the legitimate, locally-verifiable checks (`isGist`, `isDotCom`, `isGHE`, an existing stored account match), it falls back to inspecting the `wwwauth[...]` fields that git forwards from the server's HTTP response:

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

The `wwwauth[]` value is the literal content of the `WWW-Authenticate` header returned by whatever server responded to the 401 for the git operation — a value fully under the control of the remote being cloned/fetched/pushed, or of a MITM proxy sitting on an insecure (`http://`) transport. There is no verification that the host is actually a known/legitimate GitHub Enterprise instance (that would normally be done via `isGHES`/`isGHE`/network probe `isGitHubHost`, see `app/src/lib/endpoint-capabilities.ts` lines 47-68).

This classification then drives `getCredential()` ( [2](#0-1) ): when `endpointKind !== 'generic'` and no stored account matches the endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` instead of the generic username/password prompt.

`promptForGitHubSignIn` ( [3](#0-2) ) takes the attacker-controlled `endpoint`'s origin and, since its hostname isn't `github.com`, calls `dispatcher.beginEnterpriseSignIn(cb)` followed by `dispatcher.setSignInEndpoint(origin)` — launching Desktop's native "Sign in to GitHub Enterprise" dialog (`PopupType.SignIn`) pointed at the attacker's own host, all rendered with Desktop's trusted chrome and copy that says the user is signing in to a "GitHub Enterprise" instance.

The broken invariant is identical in shape to the audit finding: a value that should be an authoritative identity/policy selector (`strategyId` in the audit; "is this really a GitHub/GHE host" here) is instead taken from untrusted, attacker-supplied input, and the code performing the sensitive action (approving max LTV / launching a GitHub-branded credential collection flow) never re-validates that the selector actually corresponds to the real object it's being applied to.

### Impact Explanation
An attacker who controls a git remote (or can inject an HTTP response, e.g. via an insecure `http://` remote or a compromised proxy in the request path) can make Desktop present its native "Sign in to GitHub Enterprise" dialog for that attacker's own domain. A user who doesn't scrutinize the endpoint shown in the dialog and enters GitHub-style credentials/PAT is directly submitting them to the attacker's server — this is credential exfiltration facilitated by Desktop's own trusted UI mislabeling an arbitrary host as a legitimate GitHub Enterprise endpoint. It also causes Desktop to treat that endpoint as GitHub-flavored for subsequent operations (stores the resulting "account" if sign-in appears to succeed against the attacker's fake API responses), polluting the account store with a spoofed enterprise account.

### Likelihood Explanation
Requires only that the user clone/fetch/push against a repository URL the attacker controls, or that the connection traverses an attacker-controlled proxy on an insecure transport — no local access, no admin rights, no pre-existing malware, and no unnatural extra steps beyond the normal act of adding/using a git remote, which is squarely within the "attacker controls a git remote/proxy response" valid-impact category.

### Recommendation
Do not derive trust/classification (`'enterprise'` vs `'generic'`) from the server-supplied `WWW-Authenticate` realm string. Only classify an endpoint as GitHub/Enterprise via locally authoritative checks (`isDotCom`, `isGHE`, an already-known stored account, or an explicit user-configured GHE endpoint / verified `isGitHubHost` probe using the GitHub API contract, not free-text header matching). At minimum, before invoking `promptForGitHubSignIn` for a `wwwauth`-classified `'enterprise'` result, require corroborating evidence (e.g., a successful `isGitHubHost()` API probe) rather than trusting the header content alone.

### Proof of Concept
1. Attacker stands up a plain HTTP(S) git server (or MITMs an insecure `http://` remote) at `https://evil.example.com/repo.git`.
2. Victim adds this as a remote in GitHub Desktop and performs a fetch/pull/push.
3. Git requests credentials; the server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper trampoline.
5. `getEndpointKind()` matches `v.includes('realm="GitHub"')` and returns `'enterprise'` ( [4](#0-3) ).
6. `getCredential()` finds no stored account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
7. `promptForGitHubSignIn` opens Desktop's "Sign in to GitHub Enterprise" popup bound to `origin = https://evil.example.com` ( [5](#0-4) ).
8. Victim, believing this is a legitimate GitHub Enterprise sign-in prompted by Desktop, enters credentials/PAT, which are sent to the attacker's server.

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
