## Title
GitHub Desktop's credential-helper trusts an attacker-controlled `WWW-Authenticate` realm to classify a host as GitHub Enterprise, leading to real OAuth token exfiltration to a malicious remote/proxy - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Desktop's Git credential-helper trampoline decides whether a remote host should be treated as a trusted GitHub/GHE endpoint (and therefore whether to hand out a stored account's OAuth token) using several signals. Most of those signals verify the host's real identity (hostname allow-lists, or an actual network probe). One signal does not: it trusts the literal contents of the `WWW-Authenticate` HTTP header that Git forwards from the remote server, and if the realm string equals `"GitHub"` it classifies the endpoint as `'enterprise'` with no hostname check at all. Since that header comes straight from the remote server's HTTP response, any attacker who controls the Git remote or a proxy in the path can force this classification and trigger Desktop's GitHub sign-in flow for their own server, ultimately causing Desktop to hand the user's real GitHub OAuth token to the attacker's host as Basic Auth credentials during the Git HTTP transaction.

### Finding Description
`getEndpointKind` is used by the credential-helper to decide how a `get` credential request coming from Git should be resolved: [1](#0-0) 

Note the difference in trust level between the checks:
- `isGist(endpoint)`, `isDotCom(endpoint)`, `isGHE(endpoint)` — these validate the actual hostname of `credentialUrl`.
- `isGitHubHost(endpoint)` (fallback at the end) — this performs a real verification (a network probe) before trusting the host.
- The `wwwauth[...]` branch — this trusts a **string sent by the remote server itself** (forwarded into the credential protocol by Git as `wwwauth[]=` lines) with **no hostname verification whatsoever**. If the value contains `realm="GitHub"` the function immediately returns `'enterprise'`.

This third check is the broken invariant: it conflates "the server claims to be GitHub" with "the server is verified to be GitHub," exactly analogous to the SwiftSource bug where a raw transfer was conflated with a properly-accounted deposit. The `wwwauth` value is not something Desktop controls or verifies — it is data supplied by whatever host Git is actually talking to, which is attacker-controlled in the threat model described (malicious git remote/proxy response).

The consequence of this misclassification is visible in `getCredential`: [2](#0-1) 

Here, `endpoint`/`apiEndpoint` are derived from the real (attacker) URL Git is contacting, so no existing account will match it. Because `endpointKind !== 'generic'` (it was forced to `'enterprise'`), Desktop calls `ui.promptForGitHubSignIn(endpoint)` — a "Sign in to GitHub" prompt — for a host that is not actually GitHub or GHE. If the user proceeds (either by re-authenticating or selecting an already-signed-in account, which is the common case for Desktop power users), the real account object (with its real OAuth `token`) is merged into the credential map: [3](#0-2) 

That credential is then formatted and returned to Git via the trampoline TCP protocol: [4](#0-3) 

Git will use this `username`/`password` (the user's real GitHub login and OAuth token) as Basic Auth credentials for whatever HTTPS host it is actually talking to — the attacker's server, not github.com.

### Impact Explanation
This is unprivileged, remote-triggerable credential exfiltration: an attacker who controls a Git remote URL a victim adds/clones/fetches (or a network proxy able to inject a `WWW-Authenticate: ... realm="GitHub"` response header on any HTTPS 401) can cause GitHub Desktop to disclose the user's live GitHub OAuth token to that attacker's server. Unlike the browser-based OAuth flow (protected by `installSameOriginFilter`/`installAliveOriginFilter` per `app/src/main-process/main.ts:341-346`), this path is entirely inside the native `dugite`/trampoline credential flow and is not covered by those same-origin protections. A leaked OAuth token grants the scopes Desktop requests, enabling repository read/write, and depending on scopes, broader account access.

### Likelihood Explanation
The trigger requires only that the victim performs a normal Git operation (clone/fetch/push/pull) against a repository whose remote responds over HTTPS with a spoofed `WWW-Authenticate` realm — something fully controlled by anyone who can operate (or MITM/proxy) the Git server the victim points Desktop at. No local access, no malware, no unusual user action beyond normal Desktop usage (add remote/clone + confirm a sign-in prompt) is required, matching the required "attacker controls ... a git remote/proxy response" primitive.

### Recommendation
Do not classify an endpoint as GitHub/enterprise based solely on the attacker-suppliable `WWW-Authenticate` realm string. At minimum, corroborate the `wwwauth` heuristic with an actual hostname allow-list or the existing `isGitHubHost()` network verification (the same check already used as a fallback) before returning `'enterprise'`. If the "known GitHub host" fact cannot be independently verified, treat the endpoint as `'generic'` so Desktop's OAuth accounts are never used as credentials for it, and the "Sign in to GitHub" prompt should never be triggered for a host that hasn't been independently verified to be a real GitHub/GHE endpoint.

### Proof of Concept
1. Attacker hosts an HTTPS Git server (or sits as a MITM/corporate proxy) at `https://evil.example.com/victim/repo.git`.
2. Victim adds this URL as a remote in GitHub Desktop and performs a fetch/clone/pull that requires authentication.
3. Git sends an HTTPS request; the attacker's server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git invokes Desktop's credential helper (`CREDENTIALHELPER get`), forwarding `wwwauth[0]=Basic realm="GitHub"` on stdin, per `app/src/lib/trampoline/trampoline-credential-helper.ts:220-248`.
5. `getEndpointKind` (lines 137-179) matches the `realm="GitHub"` regex and returns `'enterprise'` without checking that `evil.example.com` is an actual GitHub host.
6. `getCredential` (lines 93-125) finds no account for `evil.example.com`, so it calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
7. Victim (already signed in to their real GitHub.com account in Desktop, or completing a fresh legitimate github.com OAuth flow) selects/confirms their account.
8. `credWithAccount` (lines 47-57) merges the real account's `login`/`token` into the credential, which is returned to Git.
9. Git authenticates to `evil.example.com` using `username=<victim's GitHub login>` and `password=<victim's real GitHub OAuth token>` — the attacker now has the victim's token.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L220-248)
```typescript
export const createCredentialHelperTrampolineHandler: (
  store: AccountsStore
) => TrampolineCommandHandler = (store: Store) => async command => {
  const firstParameter = command.parameters.at(0)
  if (!firstParameter) {
    return undefined
  }

  const { trampolineToken: token } = command
  const input = parseCredential(command.stdin)

  if (__DEV__) {
    debug(
      `${firstParameter}\n${command.stdin
        .replaceAll(/^password=.*$/gm, 'password=***')
        .replaceAll(/^(.*)$/gm, '  $1')
        .trimEnd()}`
    )
  }

  try {
    if (firstParameter === 'get') {
      const cred = await getCredential(input, store, token)
      if (!cred) {
        const endpoint = `${getCredentialUrl(input)}`
        info(`could not find credential for ${endpoint}`)
        setHasRejectedCredentialsForEndpoint(token, endpoint)
      }
      return cred ? formatCredential(cred) : undefined
```
