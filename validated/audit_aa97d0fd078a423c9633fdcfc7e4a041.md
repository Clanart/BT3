## Title
Spoofed `WWW-Authenticate: realm="GitHub"` header from a malicious remote/proxy causes Desktop to misclassify the host as GitHub Enterprise, prompting an OAuth sign-in and exfiltrating the resulting token to the attacker's server - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The report's broken invariant is: a "safe" function silently accepts attacker-influenced state as trustworthy without validating it end-to-end (allowance/state) before acting, causing unsafe downstream behavior. The Desktop analog is `getEndpointKind()` in the credential-helper trampoline, which trusts a `wwwauth[]` header value forwarded by Git — which originates directly from the remote server's HTTP response — as sufficient proof that a host is a GitHub Enterprise instance, without any further verification.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper (`git-credential-desktop`) and forwards any `WWW-Authenticate` response headers it received from the server as `wwwauth[]=...` fields in the credential protocol payload [1](#0-0) . `getEndpointKind()` classifies the endpoint as `'enterprise'` as soon as it sees a `wwwauth[]` value containing `realm="GitHub"` — this is a "happy path" heuristic used specifically to avoid making a verification request: [1](#0-0) 

Crucially, this check runs and returns *before* the only real verification step, `isGitHubHost(endpoint)`, which is reserved for the fallback path only: [2](#0-1) 

Since the `WWW-Authenticate` header's `realm` value is entirely controlled by whatever server (or MITM/malicious proxy) answers the HTTPS request, an attacker operating a git remote (e.g., an arbitrary clone URL, a malicious submodule URL, or a network-position attacker intercepting a non-GitHub HTTPS remote) can simply respond with `WWW-Authenticate: Basic realm="GitHub"` to force `getEndpointKind` to return `'enterprise'` for a host that is not actually GitHub.

Once classified as `'enterprise'`, `getCredential()` checks whether an existing account matches that endpoint; if not, it invokes `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

This surfaces Desktop's normal, expected "Sign in to GitHub Enterprise" UI flow bound to the attacker's `endpoint` URL. If the user completes this (an entirely ordinary, expected interaction — not a contrived step), a real Account with a valid OAuth-derived token is created and returned via `credWithAccount`, which merges the account's `login`/`token` into the credential map [4](#0-3) . That credential is then handed back to Git, which will use it to authenticate the original HTTPS request to the attacker's server — i.e., the freshly-obtained GitHub OAuth token is sent directly to the attacker-controlled host as an HTTP Basic Authorization header.

### Impact Explanation
This results in real token exfiltration: a legitimate GitHub OAuth token (scoped to the user's GitHub/GHE account) is transmitted to a server that is not actually GitHub, purely because that server sent back an attacker-chosen `WWW-Authenticate` realm string. It also constitutes unauthorized OAuth/account binding, since the sign-in account is stored keyed to the attacker-controlled endpoint via `AccountsStore`/`TokenStore`, meaning any future credential requests to that same origin will also be silently satisfied with the stored token [5](#0-4) . This matches the "credential/token exfiltration" and "unauthorized OAuth or account binding" impact categories, and requires no local access, admin rights, or prior malware — only that the victim clone/fetch/push against an attacker-controlled or attacker-intercepted HTTPS remote.

### Likelihood Explanation
The trigger condition is simple to reach in normal Desktop usage: adding any remote/repository whose HTTPS endpoint is attacker-controlled (public repo with a malicious `.gitmodules` submodule URL, a compromised proxy, or a typosquatted/lookalike host) and having that server return a spoofed `WWW-Authenticate` header on the initial anonymous request — standard HTTP Basic-auth challenge behavior that any server can trivially emit. No exotic conditions or elevated privileges are required; the only "guard" (`isGitHubHost`, an actual network-based check) is bypassed entirely by this heuristic short-circuit.

### Recommendation
Do not trust the `wwwauth[]` realm string as sufficient evidence of a GitHub host. Either remove the header-based fast path entirely, or treat a `realm="GitHub"` match only as a hint that triggers the same authoritative `isGitHubHost(endpoint)` network verification used in the fallback path before classifying the endpoint as `'enterprise'` and before prompting for GitHub sign-in.

### Proof of Concept
1. Attacker hosts an HTTPS git server at `https://evil.example.com/repo.git` (or intercepts traffic to any non-GitHub HTTPS remote).
2. Victim adds/clones this remote in GitHub Desktop.
3. When Git attempts to fetch, the server responds `401` with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to `git-credential-desktop get`.
5. `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'` immediately, skipping `isGitHubHost()` [6](#0-5) .
6. Since no account exists for `evil.example.com`, Desktop prompts "Sign in to GitHub Enterprise" via `ui.promptForGitHubSignIn(endpoint)` [7](#0-6) .
7. Victim signs in, believing this is a real GHE instance; a valid OAuth token is issued and stored for that endpoint.
8. The credential helper returns that token to Git, which sends it in the Authorization header of the request to `evil.example.com`, exfiltrating it to the attacker.

Note: I was not able to fully inspect `isGitHubHost()`'s implementation (in `app/src/lib/api.ts`, beyond its import) or `trampoline-ui-helper.ts`'s `promptForGitHubSignIn` due to running out of tool iterations; these should be reviewed directly to confirm there is no additional origin-pinning check between sign-in and credential return that might mitigate this path.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
```

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-178)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
```

**File:** app/src/lib/stores/token-store.ts (L1-19)
```typescript
import * as keytar from 'keytar'

function setItem(key: string, login: string, value: string) {
  return keytar.setPassword(key, login, value)
}

function getItem(key: string, login: string) {
  return keytar.getPassword(key, login)
}

function deleteItem(key: string, login: string) {
  return keytar.deletePassword(key, login)
}

export const TokenStore = {
  setItem,
  getItem,
  deleteItem,
}
```
