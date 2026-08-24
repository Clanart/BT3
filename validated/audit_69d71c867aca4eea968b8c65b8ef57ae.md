## Analog Found: GitHub Desktop trusts an unauthenticated `WWW-Authenticate` header to decide whether to treat an arbitrary git remote as a "GitHub Enterprise" endpoint

### Title
Attacker-controlled `WWW-Authenticate` realm spoofing causes GitHub Desktop to misclassify a malicious remote as a trusted GitHub Enterprise host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Sherlock finding's root cause is a **classification/trust mismatch**: the contract checks one signal (`order.asset == weth`) at match time but a downstream code path (`settleContract()`) never re-derives or acts on that same signal, producing inconsistent handling of the same underlying asset. The Desktop analog is the credential helper's `getEndpointKind()` function, which classifies an arbitrary remote host as `'github.com'`, `'ghe.com'`, `'enterprise'`, or `'generic'` using several fallback heuristics — one of which is an **unauthenticated header value forwarded verbatim by Git from the remote server itself** — and then uses that classification to decide whether to prompt the user with a "Sign in to GitHub" dialog.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential-helper trampoline (`createCredentialHelperTrampolineHandler`) with the values Git captured from the server's HTTP response, including any `WWW-Authenticate` headers. `getEndpointKind()` inspects these attacker-controlled header values before falling back to safer checks: [1](#0-0) 

Specifically:
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
This means *any* HTTPS git host — not github.com, not a recognized GHE domain, not previously paired with a Desktop account — can simply respond to Git's authentication challenge with `WWW-Authenticate: Basic realm="GitHub"` and Desktop will treat it as `'enterprise'`, i.e., a legitimate first-party GitHub host, purely on the strength of a value the remote server supplied itself.

Once classified `'enterprise'`, `getCredential()` checks whether an existing account matches that endpoint; if not, it invokes the sign-in UI flow: [2](#0-1) 

```
const endpointKind = await getEndpointKind(cred, store)
...
if (endpointKind !== 'generic' && !accounts.some(a => a.endpoint === apiEndpoint)) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
  ...
  return credWithAccount(cred, account)
}
```
`endpoint` here is derived directly from the attacker-controlled remote URL (`getCredentialUrl(cred)`), so the sign-in prompt is presented for the attacker's host, framed by Desktop as though it is a recognized GitHub Enterprise instance, purely because the attacker's server echoed a spoofable header string. This is analogous to the Sherlock bug: a single unvalidated signal (the WETH/ETH intent flag; here, the `realm="GitHub"` string) drives a trust decision in one code path (issuing an "official-looking" GitHub sign-in flow) without any independent verification that the counterpart (a genuine GitHub/GHE server) actually exists.

### Impact Explanation
A user who adds/clones from a malicious HTTPS remote (e.g., via "Clone repository" with a URL, or by opening a repository someone shared containing that remote) can be shown a Desktop-native "Sign in to GitHub" dialog for a host they have no reason to distrust, since Desktop itself vouches for it as `'enterprise'`. This falls squarely in the report's valid-impact bucket of **unauthorized OAuth/account-binding and credential exfiltration facilitated by an attacker-controlled git remote/proxy response** — the victim can be steered into an authentication flow against an attacker-controlled endpoint that Desktop's own chrome represents as trustworthy, increasing the chance that OAuth codes, PATs, or Enterprise credentials are typed into the wrong destination.

### Likelihood Explanation
The trigger requires only that the attacker control an HTTPS git server (trivial) and that the victim add/clone that remote in Desktop — no local access, no admin rights, and no prior compromise are needed, satisfying the "attacker controls ... a git remote/proxy response" criterion. The header value is not validated against DNS/TLS identity or an allowlist of known Enterprise hosts before use, so the only real precondition is that Git itself forwards the header, which it does for any 401 challenge.

### Recommendation
Do not classify an endpoint as `'enterprise'` (a trusted, sign-in-eligible classification) solely on an unauthenticated `WWW-Authenticate` header value supplied by the remote. At minimum:
- Require a positive network confirmation via `isGitHubHost(endpoint)` (already present later in the function) rather than short-circuiting on the header.
- If the header heuristic is kept for UX reasons, surface a clear "unverified" indicator in `promptForGitHubSignIn`'s UI, and avoid treating it identically to genuinely verified GHE hosts for the purposes of skipping additional verification.

### Proof of Concept
1. Attacker stands up an HTTPS git server (e.g., `https://evil.example.com/foo.git`) configured to respond to unauthenticated Git HTTP requests with `WWW-Authenticate: Basic realm="GitHub"`.
2. Attacker shares a clone URL/link for that server with a victim, who adds it in Desktop's "Clone repository" dialog or as a manual remote and performs a fetch/push.
3. Git invokes Desktop's credential trampoline; `getEndpointKind()` reads the spoofed `wwwauth[...]` value and returns `'enterprise'`. [3](#0-2) 
4. `getCredential()` finds no existing account bound to `https://evil.example.com`, and invokes `ui.promptForGitHubSignIn(endpoint)`, presenting the victim with Desktop's native GitHub sign-in flow scoped to the attacker's host. [4](#0-3) 
5. Any credentials/tokens the victim supplies during that flow are delivered to the attacker's endpoint rather than a legitimate GitHub/GHE server.

**Uncertainty**: I was unable to load `app/src/lib/trampoline/trampoline-ui-helper.ts` (tool call failed on the final iteration due to a missing `repo_name` parameter), so I could not confirm the exact UI text/flow of `promptForGitHubSignIn` or whether it independently re-validates the endpoint (e.g., via TLS cert pinning or a second network probe) before presenting credentials fields. If it does perform such secondary validation, the practical severity would be lower than described above; this should be verified directly in that file.

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
