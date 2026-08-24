### Title
Spoofed `WWW-Authenticate` realm header lets any git remote impersonate a GitHub/Enterprise host in Desktop's credential trampoline - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` in the git-credential trampoline decides whether a remote endpoint should be treated as a GitHub/GitHub Enterprise host purely by trusting a `wwwauth[...]` credential field that git forwards from the remote server's HTTP `WWW-Authenticate` response header. If that header string contains `realm="GitHub"`, the endpoint is unconditionally classified as `'enterprise'` — with no verification that the hostname, certificate, or account actually corresponds to GitHub.

### Finding Description
When git needs credentials for any remote (including arbitrary, non-GitHub hosts the user clones from or fetches), it invokes Desktop's trampoline credential helper (`createCredentialHelperTrampolineHandler`) with a "get" command. `getCredential()` first tries an exact-origin match via `getGitHubCredential`/`findGitHubTrampolineAccount` (safe, origin-exact), then falls back to `getEndpointKind()`: [1](#0-0) 

The classification loop iterates over any credential field prefixed `wwwauth[` — these values originate from the HTTP response of whatever server git is talking to — and treats the presence of the substring `realm="GitHub"` as sufficient proof that the host is a GitHub Enterprise server, *before* any hostname/DNS/TLS check is performed: [2](#0-1) 

This is structurally the same class of bug as the report's Curve oracle issue: a security-relevant classification (`N_COINS` / "is this a GitHub host") is derived from a value that is assumed to reliably describe the underlying object (the pool's real coin count / the endpoint's real identity), but that value is actually attacker-controllable and is never cross-checked against the real structure (the actual hostname, TLS identity, or a verified `/meta` API call — which the code *does* perform later, in `isGitHubHost()`, but only as a last resort after this cheap/unsafe short-circuit already returned `'enterprise'`).

Compare this to the legitimate hostname-based heuristics that follow it, which at least look at real properties of the URL (`isDotCom`, `isGHE`, `isKnownThirdPartyHost`, and finally the network probe `isGitHubHost`): [3](#0-2) 

None of those subsequent, more careful checks are reached once the `wwwauth[...]` short-circuit fires, because it returns immediately from `getEndpointKind`.

### Impact Explanation
Once `getEndpointKind()` returns `'enterprise'` for an attacker-operated remote, `getCredential()` in the same file treats the endpoint as a trusted GitHub-family host: [4](#0-3) 

If Desktop has no account matching that (attacker) endpoint, it invokes `ui.promptForGitHubSignIn(endpoint)`, which opens Desktop's native "Sign in to GitHub Enterprise" dialog for the attacker's host: [5](#0-4) 

This turns an ordinary `git fetch`/`git clone`/`git push` against any attacker-controlled or MITM'd HTTP git remote into a trigger for Desktop's own trusted-looking, GitHub-branded OAuth/basic-auth sign-in UI pointed at the attacker's server — the user is led to believe they are authenticating to a legitimate GitHub Enterprise instance because Desktop's own classification logic said so, when in fact that classification was forged by the remote server itself. Because the guard that would normally require an actual hostname/API match (`isDotCom`, `isGHE`, the account-endpoint check) is bypassed by the `wwwauth[...]` string match, the "misconfiguration" (trusting attacker-supplied realm text as identity proof) directly drives a security decision, exactly mirroring the oracle mis-binding pattern in the source report: a structural assumption about the input is never validated against the real object it's supposed to describe.

### Likelihood Explanation
Any git server (self-hosted Git server, a "clone/fetch this repo" link the user follows, or a network position capable of returning an HTTP 401 response for a plain HTTP/HTTPS remote) can set `WWW-Authenticate: Basic realm="GitHub"` on its response. Git forwards this header verbatim to the credential helper as a `wwwauth[...]` key, which desktop parses without validation. No local access, prior malware, or leaked credentials are required — only that the user perform a completely ordinary git operation (clone/fetch/push) against a remote the attacker controls or can respond on behalf of.

### Recommendation
Do not treat the `wwwauth[...]` realm text as sufficient evidence of identity. At minimum, require it to corroborate an independent identity check (matching hostname pattern, or the `isGitHubHost()` API probe) rather than short-circuiting the whole classification, so a forged `realm="GitHub"` header from an arbitrary server cannot alone flip an endpoint into the `'enterprise'`/GitHub-trusted classification and trigger the native sign-in UI.

### Proof of Concept
1. Stand up an HTTP git server (e.g., `git http-backend` behind a small proxy, or any server responding to git's smart-HTTP protocol) at `https://totally-not-github.example`.
2. Configure it to respond to the initial unauthenticated request with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. In GitHub Desktop, clone or add this URL as a remote and perform a fetch/clone.
4. Git invokes Desktop's `credential-helper get`, passing the `wwwauth[...]="Basic realm=\"GitHub\""` field through `parseCredential`.
5. `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'` for `https://totally-not-github.example`, regardless of hostname.
6. Since no existing account matches this endpoint, `getCredential()` calls `ui.promptForGitHubSignIn('https://totally-not-github.example')`, popping Desktop's native "Sign in to GitHub Enterprise" dialog — a Desktop-branded trust indicator — for a host the user never configured as GitHub Enterprise.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-130)
```typescript
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
