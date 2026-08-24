### Title
Git credential helper trusts attacker-controlled `WWW-Authenticate` realm to skip GitHub-host verification, redirecting the in-app sign-in/OAuth flow to an attacker endpoint - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The Optimism bug's root cause is a security check (`AddressAliasHelper` aliasing) that is supposed to always validate the identity of the caller, but is skipped whenever an alternate trigger path (`_isOnApproveTrigger`) is used, letting the attacker present as an address it doesn't own. GitHub Desktop's credential-helper trampoline has a structurally identical flaw: the function that is supposed to authoritatively verify "is this host really GitHub" (`isGitHubHost()`, reached via a real network round-trip) has a "happy path" that trusts a value entirely supplied by the remote server — the `WWW-Authenticate` header's `realm=` value — and short-circuits the real verification when that header contains `realm="GitHub"`, regardless of the actual hostname.

### Finding Description
`getEndpointKind()` in [1](#0-0)  determines whether a credential request should be treated as `'github.com'`, `'enterprise'`, or `'generic'`. Git forwards any `WWW-Authenticate` headers it receives from the remote to the credential helper as `wwwauth[...]` entries (this is standard git-credential protocol behavior, and the remote server fully controls its own response headers). The function does this:

```ts
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

This is explicitly documented as "a happy-path... without having to resort to making a request ourselves" — i.e., it bypasses the actual verification call `isGitHubHost(endpoint)` that appears later in the same function [3](#0-2) . Just like `_isOnApproveTrigger` bypassing `AddressAliasHelper.applyL1ToL2Alias`, this header-based shortcut bypasses the host-identity check for *any* hostname, including one entirely controlled by an attacker (a malicious git remote or a malicious proxy sitting on a legitimate connection).

The consequence flows into `getCredential()`: since the endpoint (an attacker-chosen URL/host) won't match any existing stored account, and `endpointKind !== 'generic'`, Desktop calls `ui.promptForGitHubSignIn(endpoint)` [4](#0-3) . That function opens Desktop's own trusted-looking sign-in dialog and, for anything not literally `github.com`, calls `this.appStore._beginEnterpriseSignIn(cb)` followed by `this.dispatcher.setSignInEndpoint(origin)` using the **attacker-controlled origin** [5](#0-4) . The subsequent OAuth/browser sign-in flow then builds the authorization URL from that attacker endpoint (`getOAuthAuthorizationURL`) and later exchanges the returned `code` for a token against that same attacker endpoint (`requestOAuthToken`) [6](#0-5) . Because the attacker fully controls this endpoint, they control the `/login/oauth/authorize` and `/login/oauth/access_token` pages/responses.

### Impact Explanation
An attacker who controls a git remote (or can inject/spoof responses on the network path to one, e.g., via an insecure proxy) can force GitHub Desktop into believing the remote is a legitimate GitHub Enterprise server purely by emitting `WWW-Authenticate: Basic realm="GitHub"` on an authentication challenge during clone/fetch/push — no valid GitHub API, no valid TLS cert content match, nothing beyond a header string is required. This corrupted value (the *endpoint kind*) then drives Desktop's own "Sign in with GitHub Enterprise" UI/OAuth flow toward the attacker's chosen origin, letting the attacker harvest whatever the user submits during that flow (credentials or OAuth authorization artifacts), i.e. unauthorized account binding / credential exfiltration triggered purely by fetching from a malicious remote — matching the valid-impact class ("attacker controls ... a git remote/proxy response ... unauthorized OAuth or account binding, credential/token exfiltration").

### Likelihood Explanation
The trigger requires nothing beyond the user performing an ordinary git operation (clone/fetch/push) against an attacker-supplied remote URL or a remote whose traffic passes through an attacker-influenced proxy that can add an auth challenge header — a realistic, low-effort attack path with no local access, no admin rights, and no pre-existing malware or leaked credentials needed.

### Recommendation
Remove the `WWW-Authenticate` "happy path" short-circuit in `getEndpointKind()`, or at minimum never let it elevate trust beyond what `isGitHubHost(endpoint)`'s real verification would grant — i.e., always require the authoritative check before treating an unknown host as `'enterprise'`, exactly as the Optimism fix removed the `_isOnApproveTrigger` exception and kept only the `_sender != tx.origin` check.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or MITM proxy) at `https://evil.example.com/victim/repo.git`.
2. Victim runs `git clone https://evil.example.com/victim/repo.git` (or adds it as a remote and fetches) inside GitHub Desktop.
3. On the authentication challenge, the attacker's server responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this header to Desktop's credential helper as `wwwauth[0]=Basic realm="GitHub"` via stdin, parsed by `parseCredential` [7](#0-6) .
5. `getEndpointKind()` matches the `realm="GitHub"` substring and returns `'enterprise'` without ever calling `isGitHubHost()` [8](#0-7) .
6. `getCredential()` finds no existing account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')` [4](#0-3) .
7. Desktop opens its "Sign in to GitHub Enterprise" dialog bound to the attacker's origin and starts the OAuth authorize/token exchange against it [9](#0-8) , [6](#0-5) , allowing the attacker's server to capture whatever the user submits.

Note: I could not locate an `isGitHubHost` implementation in the indexed portion of the codebase (only its usages), so I cannot fully confirm what network verification it performs before being bypassed by the `wwwauth` shortcut; this may be excluded from the index due to size limits. Starting a Devin session would allow inspection of the complete file if further verification of `isGitHubHost`'s exact behavior is needed.

### Citations

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

**File:** app/src/lib/api.ts (L2357-2395)
```typescript
export function getOAuthAuthorizationURL(
  endpoint: string,
  state: string
): string {
  const urlBase = getHTMLURL(endpoint)
  const scope = encodeURIComponent(oauthScopes.join(' '))

  return new window.URL(
    `/login/oauth/authorize?client_id=${ClientID}&scope=${scope}&state=${state}`,
    urlBase
  ).toString()
}

export async function requestOAuthToken(
  endpoint: string,
  code: string
): Promise<string | null> {
  try {
    const urlBase = getHTMLURL(endpoint)
    const response = await request(
      urlBase,
      null,
      'POST',
      'login/oauth/access_token',
      {
        client_id: ClientID,
        client_secret: ClientSecret,
        code: code,
      }
    )
    tryUpdateEndpointVersionFromResponse(endpoint, response)

    const result = await parsedResponse<IAPIAccessToken>(response)
    return result.access_token
  } catch (e) {
    log.warn(`requestOAuthToken: failed with endpoint ${endpoint}`, e)
    return null
  }
}
```

**File:** app/src/lib/git/credential.ts (L3-36)
```typescript
export const parseCredential = (value: string) => {
  const cred = new Map<string, string>()

  // The credential helper protocol is a simple key=value format but some of its
  // keys are actually arrays which are represented as multiple key[] entries.
  // Since we're currently storing credentials as a Map we need to handle this
  // and expand multiple key[] entries into a key[0], key[1]... key[n] sequence.
  // We then remove the number from the key when we're formatting the credential
  for (const line of value.split(/\r?\n/)) {
    const eqIx = line.indexOf('=')
    if (eqIx === -1) {
      continue
    }

    const k = line.slice(0, eqIx)
    const v = line.slice(eqIx + 1)

    if (k.endsWith('[]')) {
      let i = 0
      let newKey

      do {
        newKey = `${k.slice(0, -2)}[${i}]`
        i++
      } while (cred.has(newKey))

      cred.set(newKey, v)
    } else {
      cred.set(k, v)
    }
  }

  return cred
}
```
