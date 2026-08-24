### Title
Untrusted server can spoof "GitHub" identity via forged `WWW-Authenticate` realm, forcing a GitHub Enterprise sign-in/account-binding prompt for an attacker-controlled host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`DynamicContractRegistry`'s flaw was that it let a value fully controlled by one party (the registry owner) silently determine trust-critical behavior (which contract address is authoritative) with no independent verification. The Desktop analog is `getEndpointKind()` in the git credential-helper trampoline: it classifies a remote git host as `'enterprise'` (a trusted GitHub-like endpoint) based solely on an HTTP `WWW-Authenticate` header that is emitted by the remote server itself, with no cryptographic or independent verification that the host is actually GitHub/GHE.

### Finding Description
When Git needs credentials for an HTTPS remote, it captures any `WWW-Authenticate` response header from the server and forwards it to Desktop's credential helper as a `wwwauth[...]` field. `getEndpointKind` trusts this attacker-suppliable value directly: [1](#0-0) 

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

Any git server the user's Desktop talks to (a malicious remote added by a cloned/fetched repository, a spoofed submodule URL, or a MITM/malicious proxy in the request path) fully controls this header and can simply emit `realm="GitHub"` on its 401 response for any hostname whatsoever — there is zero relationship enforced between the claimed realm and the actual host identity (no TLS/certificate pinning, no allowlist, no call to `isGitHubHost` in this branch). The fallback network probe `isGitHubHost()` has the identical weakness: it checks for the presence of an `x-github-request-id` response header, which the same attacker-controlled server can trivially fabricate: [2](#0-1) 

Once `getEndpointKind` returns `'enterprise'` (or `getIsGitHubHost` says yes), `getCredential` takes a materially different path than for a truly generic/unknown host: [3](#0-2) [4](#0-3) 

```
if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    ...
    const account = await ui.promptForGitHubSignIn(endpoint)
    ...
  }
  if (endpointKind !== 'generic') {
    return undefined
  }
  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
```

`promptForGitHubSignIn` then explicitly begins a **GitHub Enterprise sign-in flow bound to the attacker's origin**: [5](#0-4) 

This surfaces a "Sign in to GitHub" dialog (`isCredentialHelperSignIn: true`) for a host the app itself has now labeled as an "enterprise" GitHub instance purely on the strength of a spoofed header — even though nothing about the host was actually verified to be a GitHub product. It also forecloses the safer, non-identity-asserting fallback (`getGenericCredential`/external credential helper) that a truly unknown/generic host would receive, since the `endpointKind !== 'generic'` check short-circuits before reaching it.

### Impact Explanation
This falls in the "unauthorized OAuth or account binding" category from the valid-impact list: an attacker who controls only a git remote/proxy response (no local access, no prior malware, no leaked credentials) can make Desktop assert to the user, via its own trusted UI chrome, that an arbitrary host is a legitimate GitHub Enterprise instance and drive the user into GitHub's enterprise sign-in flow scoped to that attacker-chosen origin. Because the classification bypasses the generic-credential path, it also removes the safer default handling that would otherwise apply to unknown hosts, increasing the chance a user is coaxed into completing an authentication ceremony (PAT entry / browser OAuth) against a host they have no reason to distrust, since Desktop's own UI now represents it as "GitHub".

### Likelihood Explanation
The trigger requires only that Desktop attempt an HTTPS git operation (clone/fetch/pull/push, including submodule operations) against a server the attacker controls or has MITM'd, and that the server return a 401 with a crafted `WWW-Authenticate: ... realm="GitHub"` header — something entirely within the attacker's control and requiring zero cooperation or unusual action from the victim beyond adding/using such a remote (e.g., via a cloned repository whose `.git/config` remote, or a `.gitmodules` submodule URL, points at the attacker's host). No certificate pinning, hostname allowlist, or independent verification exists to block this.

### Recommendation
Do not derive trust/identity classification (`'enterprise'` vs `'generic'`) from data the remote server controls (`WWW-Authenticate` realm, response headers such as `x-github-request-id`). At minimum, require the host to also match a user-registered enterprise endpoint (`accounts.some(a => a.endpoint === apiEndpoint)`) before treating it as GitHub-flavored, and treat unrecognized hosts — even ones claiming a GitHub-like realm — as `'generic'` until the user has explicitly registered them as a GitHub Enterprise endpoint through the normal `validateURL`-gated sign-in flow. If a heuristic probe like `isGitHubHost` is retained, it should not be treated as authoritative for triggering an OAuth/account-binding UI flow.

### Proof of Concept
1. Attacker sets up an HTTPS git server at `https://attacker.example`, hosting a repository (or configures a malicious proxy that intercepts requests to it).
2. Victim adds/clones a repository whose remote (or a `.gitmodules` submodule entry) points to `https://attacker.example/...` and performs a `fetch`/`clone`/`pull` in Desktop.
3. When Git requests credentials for that host, `attacker.example` responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper; `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`) matches `realm="GitHub"` and returns `'enterprise'`.
5. `getCredential` sees no existing account for `apiEndpoint` and calls `ui.promptForGitHubSignIn('https://attacker.example')`, which calls `dispatcher.beginEnterpriseSignIn` and `setSignInEndpoint('https://attacker.example')`, popping the "Sign in to your GitHub Enterprise" dialog scoped to the attacker's origin (`app/src/lib/trampoline/trampoline-ui-helper.ts:87-99`).
6. The victim, seeing Desktop's own "GitHub" sign-in UI, is induced to authenticate against `attacker.example`, completing an OAuth/account-binding flow against a host that was never independently verified as GitHub — the only "proof" was a header the attacker itself sent.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-165)
```typescript

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

**File:** app/src/lib/api.ts (L2465-2484)
```typescript
  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })

    tryUpdateEndpointVersionFromResponse(endpoint, response)

    return response.headers.has('x-github-request-id')
  } catch (e) {
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
