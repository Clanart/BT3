### Title
GitHub host classification trusts attacker-controlled `WWW-Authenticate` realm, causing real GitHub credentials to be handed to a malicious remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Desktop's Git credential helper decides whether a remote is a "GitHub host" (and therefore eligible for the internal GitHub sign-in/token flow) partly by trusting the `WWW-Authenticate` HTTP header echoed back by the remote server itself, with no verification that the host is actually GitHub. This mirrors the `getStakedAmount` pattern: a security-relevant value (`endpointKind`) is derived from data the counterpart (a remote/vault) fully controls, and that value is then relied upon by more critical logic (`getCredential`) without cross-checking it against a trusted source of truth.

### Finding Description
`getEndpointKind` in [1](#0-0)  classifies a credential request's endpoint as `'github.com'`, `'ghe.com'`, `'enterprise'`, or `'generic'`. After checking known GitHub/GHE hostnames, it falls back to trusting a header supplied by the remote server: [2](#0-1) 

The comment explicitly states this is a "happy-path" heuristic used specifically to avoid the app "having to resort to making a request" to verify the host — i.e., the classification is accepted without independent confirmation. Any HTTP(S) git server (a git remote the attacker controls, or a MITM/malicious proxy sitting on a `git+https` URL the user was tricked into adding or cloning) can respond to Git's unauthenticated request with a header such as:

```
WWW-Authenticate: Basic realm="GitHub"
```

Git captures this header and forwards it to Desktop's credential helper as a `wwwauth[...]` credential field, which `getEndpointKind` reads verbatim and returns `'enterprise'` for.

That `endpointKind` value then drives `getCredential`'s trust decision in [3](#0-2) :

```
if (
  endpointKind !== 'generic' &&
  !accounts.some(a => a.endpoint === apiEndpoint)
) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
  ...
  return credWithAccount(cred, account)
}
```

Because `endpointKind` is no longer `'generic'`, the attacker's host is routed into the GitHub sign-in path instead of the generic credential path. `ui.promptForGitHubSignIn(endpoint)` will prompt/complete a GitHub sign-in for `endpoint` (the attacker-controlled host) and whatever account results is merged into the credential via `credWithAccount`, which sets `username`/`password` to the account's real login/token: [4](#0-3) 

That credential map is then formatted and returned to Git via the trampoline (`formatCredential(cred)`), and Git uses it as the `Authorization: Basic` header when talking to the attacker's host — sending the user's real GitHub token/password to a server the user never actually intended to authenticate against as GitHub, purely because that server echoed back a crafted `WWW-Authenticate` header.

This is the same broken invariant as the `getStakedAmount` report: a downstream trust/accounting function (`getStakedAmount` / `getEndpointKind`) accepts a value that the other party fully controls (`balanceOf` inflated via `delegateStake` / `wwwauth[...]` header from the remote) instead of a source that can't be manipulated by that same party, and higher-level logic (`_calculateAegisVaultAmountsInICHIVaultIncludingStaked` / `getCredential`) propagates the corrupted value into real decisions (getters used across the protocol / real OAuth-token disclosure to the attacker).

The existing guards do not stop this path:
- `findGitHubTrampolineAccount` (exact-origin match) is checked *first* in `getGitHubCredential`, but it only matches when the attacker's host equals an already-known account's origin, so it does not stop the case where no such match exists and the code falls through to `getEndpointKind`.
- `isGitHubHost(endpoint)` (a legitimate network probe) is only reached as the *last* fallback, after the header-based heuristic has already short-circuited with `'enterprise'`. The header-based branch pre-empts the more reliable network check entirely.
- `accounts.some(a => a.endpoint === apiEndpoint)` only prevents *existing* accounts' stored tokens from being auto-filled; it does not prevent a *fresh* sign-in flow from being triggered and its resulting token handed to the attacker's endpoint.

### Impact Explanation
A successful exploitation results in exfiltration of the user's real GitHub/GitHub Enterprise OAuth token to an attacker-controlled server, satisfying the "credential/token exfiltration" impact category. This occurs during a normal, expected user action (cloning/fetching from a remote whose URL was supplied by the attacker, e.g. via a malicious `git clone <attacker-url>` link, a compromised third-party git host, or a network-level proxy/MITM the app is configured to use) — no local access, malware, or leaked credentials are required, only that the remote can shape an HTTP response header.

### Likelihood Explanation
Likelihood is Medium: triggering the vulnerable code path requires the user to add/clone/fetch from an attacker-controlled or attacker-influenced HTTPS remote and then to complete a GitHub sign-in prompt (or already be authenticated such that Desktop silently supplies token/credentials for further requests to that host during a longer-lived session). Setting the `WWW-Authenticate` header is trivial for anyone running a git server or reverse proxy, so the attacker-side requirement is negligible; the main friction is that the user must go through (or have already gone through) an interactive sign-in dialog naming the attacker's host, which may raise suspicion for a careful user but is plausible in Enterprise contexts where prompts to "sign in to your Enterprise server" are routine.

### Recommendation
Do not classify a host as GitHub/Enterprise based solely on a header value supplied by the remote itself. Either:
- Remove the `wwwauth[...]` heuristic entirely and always fall back to the network-verified `isGitHubHost(endpoint)` check, or
- Treat the `wwwauth[...]` heuristic only as a hint to *skip* a redundant network probe when it already matches a *known, already-configured* account's endpoint (i.e., cross-check the realm claim against an authoritative source such as `isGitHubHost` or an existing account list) rather than a value sufficient on its own to route the endpoint into the GitHub sign-in/credential path.

```diff
   for (const [k, v] of cred.entries()) {
     if (k.startsWith('wwwauth[')) {
-      if (v.includes('realm="GitHub"')) {
-        return 'enterprise'
-      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
+      if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
         return 'generic'
       }
     }
   }
 
   const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
   if (existingAccount) {
     return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
   }
 
   if (credentialUrl.protocol !== 'https:') {
     return 'generic'
   }
 
   return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
```

### Proof of Concept
1. Attacker stands up an HTTPS git server (or MITM proxy) at `https://evil.example.com/victim/repo.git` and configures it to answer unauthenticated Git smart-HTTP requests with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. Attacker gets a victim to clone/fetch this URL in GitHub Desktop (e.g. via a shared link, a forked/typosquatted "GitHub mirror", or by MITM-ing an existing legitimate-looking HTTPS remote).
3. Git invokes Desktop's credential helper trampoline (`ASKPASS`/`CREDENTIALHELPER`), forwarding the captured `wwwauth[0]=Basic realm="GitHub"` field for `evil.example.com`.
4. `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:137-179`) matches `realm="GitHub"` and returns `'enterprise'` without ever calling `isGitHubHost`.
5. `getCredential` (`app/src/lib/trampoline/trampoline-credential-helper.ts:93-135`) sees `endpointKind !== 'generic'` and no existing account for `evil.example.com`, so it calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
6. The victim, believing this is a legitimate Enterprise-sign-in prompt tied to their clone action, completes sign-in (or an already-cached account matching that flow is used in a variant of this scenario).
7. `credWithAccount` merges the resulting real account `login`/`token` into the credential, which is returned to Git and sent as `Authorization: Basic <login>:<token>` to `evil.example.com` — leaking the user's real GitHub credential/token to the attacker.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
```

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
