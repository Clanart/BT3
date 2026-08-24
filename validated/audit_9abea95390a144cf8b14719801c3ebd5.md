### Title
Endpoint classification in the git credential helper trusts an attacker-controlled `WWW-Authenticate` header, causing GitHub-account credential flow to be applied to an unverified host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The OtterSec finding is about a Mango "Juiced" instruction accepting a caller-supplied `RootBank` account without checking it against the canonical/expected root bank for the vault, so one call site treats attacker-chosen (wrong) collateral as if it were the real one, corrupting the notional/pool-token math. The generalizable "broken invariant" is: **a security-relevant classification is derived from data that the counterparty (not the app) controls, instead of being derived from/verified against a value the app already trusts.**

The closest analog in GitHub Desktop is `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, which decides whether a git remote should be treated as a GitHub/GitHub-Enterprise endpoint (triggering the "sign in with GitHub" account-binding flow) partly by trusting the `WWW-Authenticate` header value returned by the remote/proxy itself.

### Finding Description
When Git needs credentials for a remote it invokes Desktop's credential helper (`createCredentialHelperTrampolineHandler`), which calls `getCredential` → `getEndpointKind`: [1](#0-0) 

Key excerpt of the classification logic:
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
This `wwwauth[]` value comes straight from the credential blob Git assembles for the helper, which is populated from the HTTP `WWW-Authenticate` response header sent by the remote server (or any man-in-the-middle/HTTP proxy sitting between the client and the "remote"). Nothing here verifies that the responding host is actually GitHub.com/GHE.com/a known-good GitHub Enterprise Server before trusting the `realm="GitHub"` claim — the check is a simple substring match on attacker-suppliable text.

That classification then drives account/credential handling in `getCredential`: [2](#0-1) 

If `endpointKind !== 'generic'` and no existing account matches that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` — i.e. it invites the user into the "Sign in to GitHub Enterprise" flow for a host that self-declared itself as GitHub via a spoofable header, rather than through Desktop's own vetted enterprise-endpoint detection (`isGitHubHost`, which does an actual network probe) that is only reached as a fallback later in the same function: [3](#0-2) 

The same broken-invariant pattern (trusting an attacker-observable value instead of the canonical source of truth) also shows up nearby: `findGitHubTrampolineAccount`/`findGenericTrampolineAccount` key credential lookups purely off the `host`/`url` fields taken from the credential map built from the remote's own request/response data: [4](#0-3) 

### Impact Explanation
If a user clones/adds a remote pointing at, or is redirected/proxied to, a malicious HTTP server, that server can answer authentication challenges with `WWW-Authenticate: Basic realm="GitHub"` for a domain that is not actually GitHub.com/GHE. This misclassifies the endpoint as `'enterprise'` and steers the user into the GitHub sign-in / account-binding UI (`promptForGitHubSignIn`) for that arbitrary host, rather than the generic-credential-prompt path a non-GitHub host should get. This is a form of unauthorized/spoofed "account binding" — the app's trust decision about whether a host is a GitHub-class endpoint is made from data the remote fully controls, matching the report's core theme (unverified account/identity data substituted at a critical decision point).

It does not directly leak an already-stored token to a foreign host (matching by origin still gates that in `getGitHubCredential`), so the primary damage is: (a) misleading the user into initiating GitHub OAuth/enterprise sign-in against an attacker-presented context, and (b) incorrect endpoint-kind bookkeeping that alters credential-storage behavior (`storeCredential`/`eraseCredential` only act for `'generic'` endpoints) — meaning credentials the user enters for what Desktop just told them was "GitHub Enterprise" won't be persisted the way a generic host's would, causing silently inconsistent credential handling.

### Likelihood Explanation
This requires the attacker to control (or MITM) the HTTP responses of a remote the victim's Desktop is talking to — feasible for a malicious/compromised git host, a compromised network path, or a corporate/transparent proxy an attacker controls, all of which are within the report's allowed attacker model (a git remote/proxy response). No local access, malware, or leaked credentials are needed; the trigger is simply cloning/fetching from or being redirected to the attacker's server, which is a normal, non-consenting user action.

### Recommendation
Do not classify a remote as GitHub/GitHub Enterprise based solely on the `WWW-Authenticate` realm string supplied by that same remote. Instead:
- Only use `wwwauth[]` as a weak hint to skip the network probe, and still require corroboration via the existing `isGitHubHost(endpoint)` network check (already present as a fallback) before treating the endpoint as `'enterprise'`.
- Alternatively, only trust the header for hosts already present in a Desktop-managed allowlist (known account endpoints or explicitly-added Enterprise Server URLs), analogous to the audit's remediation of sourcing the expected addresses from a verified source (`mango_group`) rather than caller input.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or MITM proxy) at `https://internal-git.evil.example`.
2. Victim adds/fetches this remote in GitHub Desktop (e.g., via a shared clone URL or a corporate proxy substitution).
3. When Git needs credentials, the attacker's server responds to the auth challenge with header `WWW-Authenticate: Basic realm="GitHub"`.
4. `getEndpointKind()` sees the `wwwauth[]` entry containing `realm="GitHub"` and returns `'enterprise'` without any host verification.
5. `getCredential()` finds no existing account for that endpoint and invokes `ui.promptForGitHubSignIn(endpoint)`, presenting the user a "Sign in to GitHub Enterprise" prompt bound to `internal-git.evil.example`, and later credential-store/erase behavior for that host is skipped because it's no longer treated as `'generic'`.

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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
