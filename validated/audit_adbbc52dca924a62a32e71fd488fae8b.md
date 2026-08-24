### Title
Attacker-controlled `WWW-Authenticate` header from a git remote is trusted to classify a host as a "GitHub" endpoint, bypassing host verification and triggering a sign-in flow bound to an attacker's server - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` decides whether a credential request refers to a `github.com`/GHE ("enterprise") host by looking for a `wwwauth[]` credential-protocol field containing `realm="GitHub"`, *before* performing any real verification of the host. Since the `wwwauth[]` value is copied verbatim from the HTTP `WWW-Authenticate` response header returned by the git server that Desktop is talking to, a malicious or compromised git remote/proxy can set that header on an arbitrary hostname and have Desktop treat it as a trusted GitHub-affiliated endpoint — exactly the same "two different parsers agreeing on attacker-crafted input" pattern as the LiFi report, where a single attacker-controlled blob is decoded as two different (and conflicting) trusted structures.

### Finding Description
`getEndpointKind` classifies the endpoint associated with a credential request: [1](#0-0) 

The relevant branch:
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
This check runs *before* the only real network-based verification (`isGitHubHost(endpoint)`), which is reached solely on the fallback path when no `wwwauth[]` matches. The `cred` map is built directly from Git's credential-helper protocol (`parseCredential`), whose fields — including `wwwauth[]` — are populated by Git from the HTTP response headers of whatever server is on the other end of the connection: [2](#0-1) 

Because `wwwauth[]` is just forwarded response header content, any git server the user connects to (a malicious/typosquatted remote, a compromised legitimate remote, or a MITM/corporate proxy sitting on the connection) fully controls its value. There is no check that the *host* of the request actually is `github.com`, a known GHE endpoint, or reachable via the real GitHub API — the string `realm="GitHub"` alone is sufficient, and it short-circuits before `isGitHubHost` is ever called.

Once classified `'enterprise'`, `getCredential()` uses this to decide whether to prompt for GitHub sign-in: [3](#0-2) 

If no existing account's endpoint matches `apiEndpoint` (true for any attacker domain, since it's not a configured account), Desktop calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` being the attacker's arbitrary URL: [4](#0-3) 

That function inspects `hostname` and, for any non-`github.com` hostname, calls `beginEnterpriseSignIn` and `setSignInEndpoint(origin)`, wiring the sign-in/OAuth flow to the attacker-controlled origin, then shows a "Sign in" dialog to the user in the context of a routine clone/fetch/push.

This is directly analogous to the LiFi bug's broken invariant: a single attacker-supplied artifact (calldata / an HTTP header) is decoded/interpreted by one code path as "trusted GitHub structure" while a different, weaker check (string match on `wwwauth[]`) is used instead of the strong one (`isGitHubHost` real API probe), letting the attacker force the trusted classification without ever passing the real verification.

### Impact Explanation
A user who clones, fetches, or pushes to a malicious or compromised git remote (or is behind a malicious/rogue proxy) can be presented with what looks like a legitimate "Sign in to GitHub Enterprise" dialog, driven entirely by the attacker's HTTP response header, with the sign-in/OAuth exchange endpoint pointed at the attacker's own server (`setSignInEndpoint(origin)`). This can lead to token/credential exfiltration or unauthorized OAuth binding to an attacker-controlled endpoint — squarely in the reported "unauthorized OAuth or account binding" / "credential/token exfiltration" impact category, and it requires only that the attacker control a git remote or a proxy response, which the user reaches by ordinary use of Desktop (adding/cloning/fetching a repository).

### Likelihood Explanation
No special user privileges, local access, or pre-existing malware are required — only that the victim add or interact with a repository whose remote is attacker-controlled or proxied by an attacker (e.g. via a corporate/public Wi-Fi MITM proxy, a compromised mirror, or a malicious fork suggested to the victim), a scenario the report's "Valid Impact" section explicitly treats as in-scope ("a git remote/proxy response").

### Recommendation
- Do not trust `wwwauth[]` realm strings to classify a host as GitHub/Enterprise; always require the network-verified `isGitHubHost(endpoint)` check (or an exact match against an already-configured, user-approved GHE account endpoint) before treating an unknown host as `'enterprise'`.
- Only use the `wwwauth[]` heuristic as a hint to *avoid* unnecessary network calls for hosts that are clearly non-GitHub (the `'generic'` branch), never to positively assert GitHub/enterprise trust.
- Ensure `promptForGitHubSignIn`/`setSignInEndpoint` cannot be triggered for a host that hasn't been validated as a genuine GitHub/GHE instance.

### Proof of Concept
1. Set up a git-over-HTTP server on `https://attacker.example/victim/repo.git` that responds to Git's credential probe (the initial unauthenticated request) with `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote (or clones it) in GitHub Desktop.
3. Git's credential-helper protocol forwards `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper.
4. `getEndpointKind()` matches the realm string and returns `'enterprise'` for `attacker.example`, without ever calling `isGitHubHost`. [5](#0-4) 
5. Since no configured account has `attacker.example` as its endpoint, `getCredential()` calls `ui.promptForGitHubSignIn('https://attacker.example/...')`. [6](#0-5) 
6. `promptForGitHubSignIn` detects `hostname !== 'github.com'`, invokes `beginEnterpriseSignIn` and binds the sign-in endpoint to the attacker's origin. [7](#0-6) 
7. The victim, believing this is a normal corporate GHE sign-in prompt triggered by cloning a repo, may complete the flow, sending credentials/OAuth material to `attacker.example`.

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

**File:** app/src/lib/git/credential.ts (L1-36)
```typescript
import { exec as git } from 'dugite'

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
