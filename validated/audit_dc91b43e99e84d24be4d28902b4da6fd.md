### Title
Credential helper trusts spoofable `WWW-Authenticate` realm to classify arbitrary remote hosts as GitHub/Enterprise, triggering GitHub sign-in flow against attacker-controlled endpoints - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Just as the ERC20 report flagged code that trusts an external contract's return value instead of independently verifying success, GitHub Desktop's credential-helper trampoline trusts a value fully controlled by the remote git server — the `WWW-Authenticate` HTTP header's `realm` string — to decide whether an arbitrary git remote should be treated as a GitHub/GitHub Enterprise host, without any cryptographic or DNS-based verification.

### Finding Description
When Git performs HTTPS authentication against a remote and receives a `401` with a `WWW-Authenticate` header, it forwards that header verbatim to Desktop's credential helper as a `wwwauth[...]` field. `getEndpointKind` in [1](#0-0)  inspects this attacker-controlled string:

```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
```

Any HTTP(S) server the user adds as a git remote can trivially emit `WWW-Authenticate: Basic realm="GitHub"` on any unauthenticated request, regardless of whether it is actually a GitHub Enterprise instance. There is no TLS certificate check, no call to the real GHE `/meta` discovery endpoint, and no DNS validation tying the realm claim to the actual host identity — the code comment even states this is used "as a happy-path... without having to resort to making a request ourselves," i.e., it deliberately substitutes an unverified external signal for independent verification.

Once classified as `'enterprise'`, `getCredential` [2](#0-1)  checks whether an existing account matches the computed `apiEndpoint`; if not, it calls `ui.promptForGitHubSignIn(endpoint)`. That helper, in `trampoline-ui-helper.ts`, treats any non-`github.com` hostname as GitHub Enterprise and initiates the full GHE sign-in flow scoped to the attacker's origin: [3](#0-2) . This launches OAuth/PAT authentication UI against the attacker's server while presenting it to the user as a "GitHub" sign-in.

### Impact Explanation
This directly matches the "unauthorized OAuth or account binding" / "credential exfiltration" impact category: an attacker who merely controls an HTTP(S) endpoint that the user has configured as a git remote (no MITM, no privileged access) can respond to Git's authentication probe with a forged `WWW-Authenticate: realm="GitHub"` header. Desktop then surfaces a legitimate-looking "Sign in to GitHub" flow bound to the attacker's origin. If the user completes device/OAuth flow or manually enters a Personal Access Token (supported by the Enterprise sign-in dialog), that credential material is submitted to the attacker-controlled endpoint (`setSignInEndpoint(origin)` scopes all subsequent API calls to that origin), resulting in token exfiltration and false credential/account binding.

### Likelihood Explanation
Likelihood is moderate: the attacker needs the victim to add a malicious/compromised remote (fork/clone URL, an untrusted proxy, or a compromised self-hosted git server) and to trigger a fetch/push/clone against it, then complete the resulting sign-in prompt. No special privileges, no local access, and no social engineering beyond a normal add-remote/clone action are required; the header spoofing itself is trivial and fully within attacker control since Git forwards the raw header value from the HTTP response.

### Recommendation
Do not classify a remote as GitHub/Enterprise based solely on the `WWW-Authenticate` realm string. Require positive verification (e.g., a successful call to the target's `/meta` GitHub Enterprise discovery API, or matching against an explicitly configured/trusted enterprise endpoint list) before routing to the GitHub-branded sign-in flow. At minimum, the realm heuristic should only be used to narrow candidates, never as sole authority for triggering a trusted, GitHub-scoped credential UI, and the sign-in dialog should prominently display the actual origin being authenticated against.

### Proof of Concept
1. Attacker stands up an HTTPS server (`https://attacker.example`) that responds to unauthenticated Git smart-HTTP requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds `https://attacker.example/foo/bar.git` as a remote (e.g., via a shared "helpful fork" URL) and performs `git fetch`/clone through Desktop.
3. Git forwards the `wwwauth[...]` header to Desktop's credential helper; `getEndpointKind` returns `'enterprise'` for `attacker.example` [4](#0-3) .
4. Since no existing account matches `attacker.example`, Desktop calls `promptForGitHubSignIn('https://attacker.example')`, which starts the GHE sign-in flow scoped to that origin [5](#0-4) .
5. The user, believing they are authenticating to their company's GitHub Enterprise instance, completes OAuth or enters a PAT, which is transmitted to the attacker-controlled origin.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-125)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-165)
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
