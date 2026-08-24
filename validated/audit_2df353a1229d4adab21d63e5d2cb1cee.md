## Title
Loose `github.` substring heuristic in `isGitHubHost` misroutes untrusted git-remote credential requests into the trusted "GitHub Enterprise sign-in" flow, letting an attacker-controlled remote phish the user's Enterprise credentials - (File: `app/src/lib/api.ts`)

## Summary
This is a real Desktop analog of the reported class of bug: a value/flow that is supposed to go to one strictly-defined, trusted destination is instead routed based on a broad, attacker-influenceable classification rule that can send it to other destinations. In the Reserve report, RSR meant only for `StRSR` was instead spread across the `Distributor`'s destination table because the code relied on a generic downstream mechanism instead of a direct, exclusive transfer. In Desktop, credential-helper requests meant to be handled as `generic` (username/password sent nowhere trusted, or prompted per-host) are instead classified as trusted `enterprise`/GitHub hosts by a weak regex, causing Desktop to invoke the same UI/OAuth machinery normally reserved for real GitHub Enterprise servers against an attacker-chosen host.

## Finding Description
The trampoline credential helper decides how to handle a `git credential` request for a given remote endpoint via `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts`: [1](#0-0) 

When the endpoint is not `github.com`/`ghe.com`/`gist`, and there's no `WWW-Authenticate: realm="GitHub"` header and no existing matching account, it falls back to `isGitHubHost(endpoint)` in `app/src/lib/api.ts`: [2](#0-1) 

The critical broken invariant is this heuristic:
```js
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```
This regex only checks that a hostname *label* equal to `github` exists somewhere in the hostname (at the start or after a dot). It does not verify ownership of the `github.com`/`.ghe.com` domain, nor does it fall back to the network `meta` probe once it matches. Any attacker who controls a git remote hostname such as `github.attacker.com`, `attacker-github.io` won't match (needs exact label `github`), but `github.evil.com`, `foo.github.evil.com`, or `github.internal-phish.net` will match and immediately return `true` — no HTTP round-trip, no certificate/identity check.

Back in `getCredential` (`trampoline-credential-helper.ts:94-135`): [3](#0-2) 

If `endpointKind !== 'generic'` and no signed-in account matches the derived `apiEndpoint`, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

Since `hostname !== 'github.com'`, this takes the `else` branch: `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` — i.e. Desktop opens the legitimate "Sign in to GitHub Enterprise" dialog/OAuth device flow, but pointed at the **attacker's origin**, because the credential-helper misclassified the attacker host as an enterprise GitHub instance.

The corrupted value here is the `endpointKind` classification (`'enterprise'` vs `'generic'`), which controls whether Desktop trusts a host enough to run its GitHub sign-in UI against it. Existing guards (`isDotCom`, `isGHE`, `WWW-Authenticate` realm sniffing, `isKnownThirdPartyHost`) do not stop this path because:
- `isKnownThirdPartyHost` only excludes a small hardcoded set (`dev.azure.com`, `gitlab.com`, `bitbucket.org`, `amazonaws.com`, `visualstudio.com`) — an attacker simply avoids those substrings.
- The `github.` substring check runs *before* any network verification (the `getEndpointVersion`/`meta` HEAD request check that would otherwise confirm `x-github-request-id`), so a spoofed hostname short-circuits straight to `true` without ever contacting the real GitHub API.

## Impact Explanation
An attacker who controls a git remote (e.g. a cloned/forked repository whose remote URL, or a linked submodule URL, points at `https://github.<attacker-domain>/...`) can cause Desktop to present the user with what looks like a legitimate "Sign in to GitHub Enterprise" prompt/OAuth flow while actually directing that authentication flow's connectivity checks and any manual entry toward the attacker's server. This matches the requested impact class: unauthorized OAuth/account-binding and credential exfiltration risk driven purely by attacker-controlled remote content, with no local access, no admin rights, and no pre-existing malware needed.

## Likelihood Explanation
Triggering the classification only requires the user to perform an ordinary interactive git operation (clone/fetch/push) against a repository whose remote hostname contains a `github.` label under attacker control — a very low bar, and the check happens automatically inside the trampoline credential helper without any unusual user action (the "background task" skip only applies to non-interactive operations, so interactive clone/push/fetch will trigger the prompt).

## Recommendation
Remove or replace the bare substring/label heuristic (`/(^|\.)(github)\./`) in `isGitHubHost` (`app/src/lib/api.ts`) with logic that only trusts hostnames verified either by exact match against configured/known GHE endpoints or by the authenticated network probe (the existing `meta` HEAD request checking `x-github-request-id`) — i.e., never return `true` based on hostname string content alone before that verification step.

## Proof of Concept
1. Attacker publishes a repository with a remote (or submodule) URL such as `https://github.evilcorp.io/org/repo.git`.
2. Victim clones/adds this remote in GitHub Desktop and performs a fetch/push that requires authentication (no account configured for `evilcorp.io`).
3. Git invokes the trampoline `credential get` command; `getEndpointKind` reaches the `isGitHubHost` fallback (`app/src/lib/api.ts:2452`), the regex `/(^|\.)(github)\./` matches `github.evilcorp.io`, returning `true` without any network validation.
4. `getCredential` treats this as `'enterprise'`, finds no matching account, and calls `ui.promptForGitHubSignIn('https://github.evilcorp.io')` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-99`), which opens the trusted-looking "Sign in to GitHub Enterprise" popup/OAuth flow bound to the attacker's origin, exactly as it would for a real GHES instance.

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

**File:** app/src/lib/api.ts (L2429-2463)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
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

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
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
