### Title
Broken hostname pattern check in `isGitHubHost` lets an attacker-controlled domain be silently trusted as a GitHub host - ([File: app/src/lib/api.ts])

### Summary
`isGitHubHost()` is the function used by the credential-helper trampoline to decide whether a git remote host is "GitHub" (and should therefore be handled via GitHub sign-in / stored account tokens) or "generic" (handled via the generic/external credential path). One of its fallback checks is a bare regex test on the hostname, and that regex accepts far more than actual GitHub domains, similar in spirit to Nomad's flawed "acceptable root" check that treated a value it should not have accepted as valid.

### Finding Description
`isGitHubHost` runs a series of increasingly weak heuristics before falling back to a network probe: [1](#0-0) 

The line
```
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```
matches any hostname where the literal substring `github.` appears either at the very start or immediately after a dot — with no requirement that it be the last (registrable) label. An attacker-registered domain such as `x.github.attacker.io` or `sub.github.evil.com` satisfies this regex and is classified as a trusted GitHub host purely from a string pattern, without ever reaching the `getEndpointVersion`/`meta` network check that would have correctly identified it as non-GitHub.

This function feeds directly into the git credential helper's trust decision: [2](#0-1) 

`getEndpointKind` uses `isGitHubHost(endpoint)` to decide between `'enterprise'` and `'generic'`. If it wrongly returns `true` for an attacker's host, `getCredential()` takes the "trusted GitHub" branch: [3](#0-2) 

Since no stored `Account` exists for the fabricated endpoint, this calls `ui.promptForGitHubSignIn(endpoint)`, which — because the hostname isn't literally `github.com` — routes into the **Enterprise sign-in flow** and scopes it to the attacker's origin: [4](#0-3) 

The corrupted value here is the trust classification returned by `isGitHubHost`/`getEndpointKind` — a boolean/enum that should only be `true`/`'enterprise'` for a legitimate GitHub Enterprise host, but is instead set by a naive substring/regex pattern match on attacker-supplied hostnames.

### Impact Explanation
When Desktop performs a git operation (clone/fetch/push) against an attacker-controlled remote whose hostname matches the `github.` pattern, the app presents what looks like an official "GitHub sign-in" dialog for that host, and the resulting authentication flow (`beginEnterpriseSignIn` + `setSignInEndpoint(origin)`) is scoped to the attacker's origin. This can lead a user to enter their GitHub Enterprise credentials/OAuth flow against a server the attacker controls, resulting in credential exfiltration or in Desktop associating account tokens with the wrong (malicious) endpoint. This matches the requested impact class of "unauthorized OAuth or account binding" / "credential exfiltration" driven by attacker-controlled remote data (the git remote URL/hostname).

### Likelihood Explanation
The trigger requires only that a victim add or clone a repository whose remote URL hostname contains `github.` as a label boundary (e.g. `github.attacker.io`, `x.github.evil.com`) — something fully within an unprivileged attacker's control when they host the malicious remote and get the user to add/clone it (a normal, expected Desktop workflow, not requiring local access, malware, or leaked credentials). The existing "meta endpoint" network probe, which is the actually trustworthy check, is skipped entirely once the regex short-circuits with `true`, so no server-side validation ever occurs for these crafted hostnames.

### Recommendation
Replace the loose regex with a proper hostname/label check that only matches the registrable domain (e.g., verify the hostname's last two/three labels equal `github.com`, `*.ghe.com`, or a configured Enterprise host list), and never trust hostname string patterns alone for the enterprise-vs-generic credential decision — always require the network `meta` verification (`x-github-request-id`) for any host that isn't an already-known/configured endpoint, i.e. remove the early-return `true` in the `/(^|\.)(github)\./` branch or replace it with a strict suffix match (`hostname === 'github.com' || hostname.endsWith('.github.com')`, etc.).

### Proof of Concept
1. Attacker hosts a git server at `https://x.github.attacker.io/evil.git`.
2. Victim adds/clones this URL in GitHub Desktop.
3. Desktop invokes the git credential helper trampoline; `getCredentialUrl` extracts host `x.github.attacker.io`.
4. `getEndpointKind` calls `isGitHubHost('https://x.github.attacker.io')`; none of `isDotCom`/`isGHE`/known-third-party checks match, but `/(^|\.)(github)\./.test('x.github.attacker.io')` returns `true`, so the function returns `true` without ever making the `meta` verification request.
5. `getCredential` finds no existing `Account` for this endpoint and calls `ui.promptForGitHubSignIn('https://x.github.attacker.io')`.
6. Because `hostname !== 'github.com'`, Desktop calls `beginEnterpriseSignIn` and `setSignInEndpoint(origin)` with the attacker's origin, presenting an "Enterprise sign in" dialog scoped to the attacker's server.
7. Any credentials/OAuth flow the victim completes there are directed at the attacker-controlled origin rather than a legitimate GitHub Enterprise instance.

Note: I was unable to fully trace `getEnterpriseAPIURL`/`isDotCom`/`isGHE` implementations and the exact OAuth vs. basic-auth mechanics of `beginEnterpriseSignIn` within the tool budget available, so the precise credential format exfiltrated (OAuth device code vs. username/password) is not fully confirmed — this would need direct inspection of `app/src/lib/stores/sign-in-store.ts` and `app/src/lib/api.ts`'s enterprise endpoint helpers, which the indexed context did not fully expose.

### Citations

**File:** app/src/lib/api.ts (L2435-2454)
```typescript
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
