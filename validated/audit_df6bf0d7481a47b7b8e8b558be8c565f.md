### Title
Malicious Git server/proxy can spoof a `WWW-Authenticate: realm="GitHub"` header to trigger a scoped GitHub Enterprise sign-in against an attacker-controlled endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's credential-helper trampoline decides whether a Git remote is a "GitHub host" (and therefore worth prompting the user to sign in to) using several heuristics. One of these heuristics blindly trusts an HTTP `WWW-Authenticate` header value that is returned by the remote server itself, with no cross-check against the real GitHub endpoint-discovery logic that exists elsewhere in the codebase. Any attacker-controlled Git remote or HTTPS proxy sitting in the connection path can therefore force Desktop's credential helper to classify the host as `'enterprise'` simply by responding with `WWW-Authenticate: realm="GitHub"`, which causes Desktop to launch a full GitHub Enterprise sign-in flow scoped to that attacker's URL.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` is used by the `get` credential-helper handler (`getCredential`) to decide how to respond to a Git credential request: [1](#0-0) 

The relevant branch is:

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
``` [2](#0-1) 

This value is fed straight from the `wwwauth[...]` credential fields, which Git populates from HTTP `WWW-Authenticate` response headers sent by whichever server (or proxy/MITM) answered the auth challenge for the operation (comment at lines 153-156 confirms this is server-controlled data). Unlike the later, legitimate check in the same function — `isGitHubHost`, which performs an out-of-band network probe (looking for `x-github-request-id`) and hostname-pattern heuristics before ever calling a URL a "GitHub" host — the `wwwauth` shortcut requires no verification at all: [3](#0-2) 

Once `getEndpointKind` returns `'enterprise'`/`'github.com'`/`'ghe.com'` for an endpoint that has no matching stored account, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

which, for any non-`github.com` hostname, opens the GitHub Enterprise sign-in flow scoped explicitly to the attacker's origin: [5](#0-4) 

This is triggered purely as a side effect of a normal Git network operation (clone/fetch/pull/push, including recursive submodule fetches) against a repository whose remote (or an intermediate HTTP proxy) is under attacker control — the invariant "only endpoints Desktop has independently verified as GitHub should trigger a GitHub sign-in prompt" is broken because that verification (`isGitHubHost`) is bypassed entirely by the cheap, unauthenticated `wwwauth` shortcut.

### Impact Explanation
A user cloning or fetching from a malicious/compromised remote (or passing through a malicious proxy) will be shown a "Sign in to your GitHub Enterprise" dialog whose configured endpoint is the attacker's own server. `EnterpriseServerEntry`/`AuthenticationForm` supports basic username/password authentication for GHE instances (as reflected in `sign-in-store.ts`'s `setEndpoint`/`Authentication` flow). If the user completes this prompt believing it to be a legitimate re-authentication requested by their own Git operation, Desktop will submit the user's GitHub Enterprise username and password directly to the attacker-controlled endpoint, resulting in credential exfiltration and an account bound to a spoofed endpoint. This satisfies the report's valid-impact bar: the attacker only needs control of a git remote/proxy response, and the result is credential/token exfiltration and unauthorized account binding — no local access, malware, or leaked secrets are required.

### Likelihood Explanation
Any Git server (including one added transparently as a URL-rewritten `insteadOf`, a submodule remote, or an HTTP(S) proxy on the network path) can trivially add a `WWW-Authenticate: realm="GitHub"` header to a 401 response for a Git-over-HTTP request; this requires no special server software, just a header on an authentication challenge. Because the check happens before the safer `isGitHubHost` network verification and does not require any prior state (no account, no config) the bar to trigger it is low, mirroring the original report's pattern of trivially satisfying a supposedly meaningful precondition (there, sending 1 wei; here, adding one HTTP header).

### Recommendation
Do not classify a host as a GitHub/GHE endpoint based solely on the `WWW-Authenticate` realm string returned by the remote. At minimum, corroborate the `wwwauth` hint with the same `isGitHubHost` network verification (checking for `x-github-request-id` from the `/meta` endpoint) used later in the function before offering a GitHub sign-in prompt, and clearly surface the true destination host in the sign-in dialog so users can recognize an unexpected/untrusted endpoint before entering credentials.

### Proof of Concept
1. Stand up an HTTPS server (or a MITM/forward proxy) at `https://evil.example.com` that serves a fake Git repository (or simply intercepts an existing legitimate remote's traffic).
2. Configure it to respond to Git's HTTP auth probe (`GET .../info/refs?service=git-upload-pack`) with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. Have the victim `git clone https://evil.example.com/foo/bar.git` in GitHub Desktop, or add `evil.example.com` as a remote/submodule of an otherwise normal repository and perform a fetch/pull.
4. Git invokes the `desktop` credential helper's `get` command with `wwwauth[0]=Basic realm="GitHub"`; `getEndpointKind` returns `'enterprise'` at [6](#0-5)  without any real verification.
5. Since no account matches `apiEndpoint` for `evil.example.com`, Desktop calls `ui.promptForGitHubSignIn('https://evil.example.com')`, opening the "Sign in to your GitHub Enterprise" dialog pre-scoped to `evil.example.com` [7](#0-6) .
6. If the user submits basic-auth credentials in that dialog, they are sent to `evil.example.com`, giving the attacker the user's GitHub Enterprise username/password.

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

**File:** app/src/lib/api.ts (L2429-2463)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
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
