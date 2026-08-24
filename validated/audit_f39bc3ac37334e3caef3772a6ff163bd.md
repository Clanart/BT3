### Title
`isGitHubHost` misclassifies attacker-controlled hosts as GitHub/Enterprise via loose substring/prefix regex, enabling GitHub-Enterprise credential-prompt spoofing - (File: `app/src/lib/api.ts`)

### Summary
The Umee bug is a case of a security decision (price = $1) being made from a loose substring test on a string that an outside party can shape (`strings.Contains(denom, "USD")`). The same broken-invariant pattern exists in GitHub Desktop's endpoint-trust logic: `isGitHubHost()` in `app/src/lib/api.ts` decides whether a remote host should be trusted as a GitHub/GitHub Enterprise endpoint using a loose regular expression match on the hostname rather than validating the actual registrable domain, and this classification feeds directly into the git credential-helper trampoline flow.

### Finding Description
`isGitHubHost` classifies a URL as GitHub-owned using several heuristics, one of which is a bare pattern match on the hostname string: [1](#0-0) 

`/(^|\.)(github)\./.test(hostname)` returns `true` for any hostname that begins with, or has a label equal to, the literal string `github` followed by a dot — including hostnames the attacker fully controls and that have nothing to do with GitHub, e.g. `github.attacker.com` or `github.evil.example`. This is the same class of flaw as the Umee `strings.Contains(denom, "USD")` check: a trust decision derived from a naive string pattern instead of a verified identity (a real DNS suffix under GitHub's control, a pinned enterprise endpoint, or a certificate-backed handshake).

This function is consumed by the git-credential trampoline used for every `git fetch`/`clone`/`push` credential request: [2](#0-1) 

When Git asks Desktop's credential helper for credentials on a host that is not `github.com`, not `*.ghe.com`, has no `WWW-Authenticate: realm="GitHub"` header yet, and has no existing matching account, `getEndpointKind` falls through to `isGitHubHost(endpoint)`. For a URL such as `https://github.attacker.com/...`, the regex heuristic returns `true` before any network round-trip is even attempted, so the endpoint is classified as `'enterprise'`: [3](#0-2) 

Back in `getCredential`, once the endpoint is treated as non-`'generic'` and no existing account matches it, Desktop triggers the **GitHub sign-in UI** for that attacker-controlled endpoint: [4](#0-3) 

which calls `promptForGitHubSignIn`, and because the hostname isn't `github.com`, Desktop starts a full **GitHub Enterprise sign-in flow** against the attacker's domain: [5](#0-4) 

Existing guards do not stop this path: `isDotCom`/`isGHE` in `endpoint-capabilities.ts` correctly require an exact `hostname === 'github.com'`/`'.ghe.com'` suffix, but the fallback regex in `isGitHubHost` undermines that precision by accepting any `github.`-prefixed label anywhere an attacker chooses to register it, and this fallback runs **before** the network-based `/meta` header check that could otherwise disprove the claim.

### Impact Explanation
An attacker who controls a git remote (e.g. a submodule URL, a forked repo's remote, or a crafted clone URL a user is lured into opening) can name their HTTPS git server `github.<attacker-domain>` or `<anything>.github.<tld>`-shaped host. When the victim's Desktop performs a fetch/clone against that remote, Desktop's own credential trampoline will treat the attacker's server as a legitimate GitHub Enterprise instance and proactively pop up Desktop's native "Sign in to GitHub Enterprise" dialog, pointed at the attacker's endpoint. This is a UI/trust-boundary confusion issue: it turns Desktop's own trusted chrome into a vector for presenting a GitHub-styled authentication prompt for a host the user never asked to authenticate to, increasing the risk that a user enters enterprise credentials or completes an OAuth/PAT flow against the attacker's server, or is otherwise confused about the trust relationship for a repository they are interacting with. This maps to the "unauthorized OAuth" / credential-exfiltration class the assessment scope calls out, since it is triggered purely by the attacker-controlled remote and does not require local access, admin rights, or prior compromise.

### Likelihood Explanation
The trigger requires only that the victim add or fetch from a git remote whose hostname is chosen by the attacker (trivial — any domain owner can create a subdomain label starting with `github.`). No user interaction beyond a normal `clone`/`fetch`/`pull` is required, and the credential helper runs this logic automatically as part of Git's credential-fill protocol.

### Recommendation
Remove the bare substring/prefix regex heuristic (`/(^|\.)(github)\./`) from `isGitHubHost` in `app/src/lib/api.ts`, and rely only on exact/allow-listed hostname comparisons (`isDotCom`, `isGHE`, an explicit user-registered enterprise endpoint list) plus the authenticated network probe (`x-github-request-id` check) that already exists later in the function — do not short-circuit to `true` based on hostname text alone.

### Proof of Concept
1. Attacker stands up an HTTPS git server at `https://github.attacker.example/victim/repo.git` (any DNS label the attacker owns that starts with `github.`).
2. Victim adds this as a remote (or clones a repo containing a submodule pointing at it) in GitHub Desktop.
3. On the next fetch, Git invokes Desktop's credential trampoline (`createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind`) for `https://github.attacker.example`.
4. `isDotCom`/`isGHE`/`isKnownThirdPartyHost` all return false; no cached `WWW-Authenticate` header exists yet; `isGitHubHost` short-circuits true via `/(^|\.)(github)\./.test('github.attacker.example')`.
5. `getCredential` finds no matching account for the endpoint and calls `ui.promptForGitHubSignIn('https://github.attacker.example/...')`, which opens Desktop's native GitHub Enterprise sign-in dialog pre-targeted at the attacker's domain [6](#0-5) .

Note: I could not execute the app locally to confirm the exact rendered dialog text/behavior end-to-end; this analysis is based on static code tracing of the cited functions.

### Citations

**File:** app/src/lib/api.ts (L2450-2463)
```typescript

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-125)
```typescript
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
