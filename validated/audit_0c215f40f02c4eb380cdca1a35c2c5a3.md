## Title
Enterprise `isGitHubHost` credential-endpoint check accepts a WWW-Authenticate header from *any* server the app tries to authenticate to, allowing a malicious remote/proxy to be classified as "enterprise" and receive GitHub credential flow prompts — (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The report's underlying invariant is: *"success"/classification of a target must not be trusted without verifying the target actually is what it claims to be, otherwise an attacker-controlled endpoint can piggyback on privileged handling meant for a trusted party.* In the ERC20 case, the trusted party was "a real contract"; here the trusted party is "a real GitHub/GHE host." `getEndpointKind` in `trampoline-credential-helper.ts` decides whether a credential request is treated as GitHub/GHE (and therefore triggers GitHub sign-in prompts and eventually can be satisfied with a stored GitHub `Account` token) purely from attacker-observable/attacker-suppliable signals: the `WWW-Authenticate` header text returned by the remote, and a live HTTP request via `isGitHubHost`.

### Finding Description
When a Git credential request comes in via the trampoline, `getEndpointKind` classifies the target host: [1](#0-0) 

Key steps of that classification:
1. If a `wwwauth[...]` credential field (forwarded verbatim from the remote server's `WWW-Authenticate` response header) contains the literal string `realm="GitHub"`, the endpoint is classified `'enterprise'` — i.e., treated as a GitHub Enterprise host.
2. Otherwise, if `credentialUrl.protocol === 'https:'`, the code performs a network probe `isGitHubHost(endpoint)` and, if that returns true, again classifies as `'enterprise'`.

Both of these signals originate from the remote peer, not from any credential the user configured in Desktop. Any HTTP server (a malicious/compromised git remote, a malicious HTTP(S) proxy sitting on the configured `http.proxy`/system proxy path, or a MITM position at the network layer) can freely emit `WWW-Authenticate: Basic realm="GitHub"` on a 401 response, or respond in whatever way makes `isGitHubHost` return true, for a URL that is *not* actually `github.com` or a real GHE instance. This mirrors exactly the audit's core complaint: the code checks for a "successful" signal (a matching realm string / a probe response) without any deeper verification that the target is genuinely what it claims (a contract in the ERC20 case; a genuine GitHub/GHE server here).

Because this classification feeds directly into `getCredential`, once `endpointKind !== 'generic'` the trampoline will:
- Prompt the user with a GitHub-branded sign-in dialog (`ui.promptForGitHubSignIn(endpoint)`) for the attacker's endpoint, or
- If a GitHub/GHE account already exists whose endpoint happens to match (`getAPIEndpoint(endpoint)`), silently return that account's real OAuth token to the attacker's HTTP endpoint via the credential helper protocol: [2](#0-1) 

The credential-URL itself (`getCredentialUrl`) is built from raw `protocol=`/`host=`/`path=` fields supplied by Git's credential protocol, which in turn are populated based on whatever host Git is currently talking to (including hosts reached after an HTTP redirect during clone/fetch/push, or via a proxy): [3](#0-2) 

Existing guards that do *not* stop this path:
- `findGitHubTrampolineAccount` matches by URL `origin`, which is sound *if* the endpoint string is trustworthy — but the endpoint string's classification (`'enterprise'` vs `'generic'`) is exactly what's being spoofed upstream, so origin-matching does not protect against a look-alike host being *treated* as enterprise in the first place: [4](#0-3) 
- `isDotCom`/`isGHE` checks in `getEndpointKind` (lines 145-151) only catch the case where the endpoint literally resolves to a dotcom/GHE domain; the WWW-Authenticate/`isGitHubHost` branches are precisely the fallback paths meant to catch *arbitrary* enterprise hosts and are the ones lacking verification.

### Impact Explanation
If a user has GitHub.com or GitHub Enterprise accounts signed into Desktop, an attacker who controls (or MITMs) a Git remote/proxy endpoint that Desktop connects to for an unrelated operation (fetch/clone/push against a malicious or compromised repository, or a submodule URL, or a corporate proxy under attacker control) can cause Desktop's credential trampoline to treat that endpoint as GitHub Enterprise. Depending on downstream account-endpoint matching this can result in a GitHub sign-in prompt being shown for an attacker-controlled host (phishing surface dressed up as a "GitHub" auth prompt) or, if endpoint matching in `getAPIEndpoint`/account store aligns, a stored OAuth token being handed to the malicious credential-helper response path. This is a credential/token exposure primitive matching the report's "valid impact" bucket (unauthorized credential/token exfiltration via an attacker-controlled remote/proxy response).

### Likelihood Explanation
Likelihood is **Low-to-Medium**: it requires the victim to perform a Git network operation against a host the attacker controls or intercepts (e.g., cloning/fetching a malicious repo, or an attacker with a MITM/proxy position), and it depends on `isGitHubHost`'s specific probing logic (not fully visible in the indexed code) actually returning true for a non-GitHub host, or on the attacker being able to fabricate a `WWW-Authenticate: realm="GitHub"` header, which is trivial for any HTTP server the attacker controls. No local access, no prior malware, and no unnatural user steps are required beyond a normal `git fetch`/`clone`/`push`.

### Recommendation
1. Do not classify a host as `'enterprise'` based solely on a self-reported `WWW-Authenticate` realm string; that header is fully attacker-controlled.
2. Restrict the `isGitHubHost` heuristic probe (and its trust boundary) so that a positive result only grants "enterprise" treatment for hosts the user has explicitly configured as a GHE account endpoint, rather than any arbitrary host encountered during a Git operation.
3. Ensure `getCredentialUrl`/the credential fields used for classification cannot be influenced by cross-host HTTP redirects during a single git operation (align with Git's own recommendation to disable credential propagation across redirect boundaries, e.g. `http.followRedirects=false` or origin-scoped credential handling).
4. When falling into the GitHub sign-in prompt path for a heuristically-detected "enterprise" endpoint, surface the actual endpoint URL prominently to the user before requesting credentials, so a spoofed realm/proxy cannot silently harvest a legitimate token.

### Proof of Concept
1. Attacker sets up an HTTPS server (or a MITM/corporate-proxy position for a URL Desktop will contact) that, on any Git smart-HTTP request, responds `401 Unauthorized` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim (who has an existing GitHub.com or GHE account signed into Desktop) performs `git fetch`/`clone`/`push` in Desktop against a repository whose remote URL points at the attacker's server (e.g., a malicious fork, or a submodule URL embedded in a cloned repository's `.gitmodules`, as also exercised in `app/src/lib/git/submodule.ts`'s `updateSubmodulesAfterOperation`).
3. Git invokes the credential helper trampoline; `parseCredential` captures the forwarded `wwwauth[...]` field containing `realm="GitHub"`.
4. `getEndpointKind` (lines 157-165 of `trampoline-credential-helper.ts`) matches `realm="GitHub"` and returns `'enterprise'` for the attacker's host, even though it is not really GitHub/GHE.
5. `getCredential` then either shows a GitHub-branded sign-in dialog for the attacker's endpoint (phishing) or, if `getAPIEndpoint(endpoint)` happens to coincide with a stored account's endpoint (e.g. via a crafted subdomain/path matching `getEnterpriseAPIURL` logic), returns that account's real token to be sent to the attacker's server as part of the Git credential-fill response. [5](#0-4)

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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L46-58)
```typescript
export const getCredentialUrl = (cred: Map<string, string>) => {
  const u = cred.get('url')
  if (u) {
    return new URL(u)
  }

  const protocol = cred.get('protocol') ?? ''
  const username = cred.get('username')
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const host = cred.get('host') ?? ''
  const path = cred.get('path') ?? ''

  return new URL(`${protocol}://${user}${host}/${path}`)
```

**File:** app/src/lib/find-account.ts (L55-69)
```typescript
  const parsedURL = parseRemote(urlOrRepositoryAlias)
  if (parsedURL) {
    const account =
      allAccounts.find(a => {
        const htmlURL = getHTMLURL(a.endpoint)
        const parsedEndpoint = URL.parse(htmlURL)
        return parsedURL.hostname === parsedEndpoint.hostname
      }) || null

    // If we find an account whose hostname matches the URL to be cloned, it's
    // always gonna be our best bet for success. We're not gonna do better.
    if (account) {
      return account
    }
  }
```
