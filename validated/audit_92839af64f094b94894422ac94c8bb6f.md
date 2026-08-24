### Title
Spoofable hostname heuristic lets an attacker-controlled git remote be classified as a trusted "GitHub Enterprise" host, triggering an OAuth/enterprise sign-in prompt for an attacker origin - (File: `app/src/lib/api.ts`, `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`isGitHubHost()` decides whether a remote's hostname should be treated as a legitimate GitHub/GHE host using a permissive regular expression. Any hostname that merely *starts with* `github.` (e.g. `github.attacker.com`) satisfies the check and is classified as an enterprise GitHub host, even though it is not one. This is the same broken-invariant pattern as the Vether report: an "allow/trust" decision that should require privileged/verified membership is instead granted by attacker-controlled input (a hostname the attacker fully controls by registering a domain), with no server-side verification gating it before downstream trust decisions are made.

### Finding Description
`isGitHubHost` in `app/src/lib/api.ts` contains:
```
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
``` [1](#0-0) 

The regex `(^|\.)(github)\.` matches any hostname where the substring `github.` occurs at the very start of the string, or immediately following a `.`. This is meant to catch enterprise instances named like `github.example.com`, but it equally matches attacker-registered domains such as `github.attacker.com` or `sub.github.evil.io`, since `github.` legitimately sits at position 0 or after a dot in those names too. There is no check that the remainder of the hostname belongs to a known/verified organization — the "trust" decision is entirely derived from a string an attacker chooses when buying a domain.

This heuristic feeds `getEndpointKind()` in the credential-helper trampoline:
```
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) { return true }
...
return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
``` [2](#0-1) 

which is used by `getCredential()`:
```
if (
  endpointKind !== 'generic' &&
  !accounts.some(a => a.endpoint === apiEndpoint)
) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
``` [3](#0-2) 

So when Git (via the ASKPASS/credential-helper trampoline) asks Desktop for credentials while talking to a remote whose host looks like `github.<attacker>.com`, Desktop concludes it is dealing with an "enterprise" GitHub server rather than a "generic" git host, and if no matching account exists it drives the user into the GitHub Enterprise sign-in flow (`promptForGitHubSignIn` → `beginEnterpriseSignIn`/`setSignInEndpoint`) for that attacker-controlled origin instead of the plain "generic git credentials" prompt that would normally be shown for a non-GitHub remote. [4](#0-3) 

The existing guard that *does* work correctly is `findGitHubTrampolineAccount`, which only attaches a stored token if the account's endpoint origin matches exactly:
```
return accounts.find(
  a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
)
``` [5](#0-4) 
This prevents leaking an *existing* stored token to the attacker host. What it does **not** prevent is the misclassification itself: Desktop still treats the attacker host as a first-class GitHub Enterprise identity and invites the user through the enterprise/OAuth sign-in UX rather than the plain generic-credentials prompt, upgrading the perceived trust level of a host the attacker fully controls.

### Impact Explanation
The attacker only needs to control the DNS/hostname of a git remote the user clones or fetches from (`git remote or proxy response` category) — no local access, no leaked credentials, and no admin rights are required. By choosing a domain of the form `github.<anything>.<tld>`, the attacker causes GitHub Desktop to:
1. Skip the normal "generic git credential" prompt and instead present the "Sign in to GitHub Enterprise"/OAuth flow, which is a stronger trust signal to the user and can be used to phish GitHub credentials or an OAuth authorization against the attacker's own OAuth-compatible endpoint.
2. Potentially cause an account to be created/bound (`setSignInEndpoint`) against a spoofed enterprise host, since the endpoint used for the sign-in flow is derived directly from the untrusted remote URL.

This does not directly exfiltrate an already-stored token (that path is protected by exact-origin matching in `findGitHubTrampolineAccount`), but it does allow an attacker to elevate an arbitrary host's perceived trust level and steer the user into an authentication flow they would not otherwise see, which is the credential/authentication-trust analog of Vether's "self-added to the trusted/excluded list."

### Likelihood Explanation
High for occurrence, moderate for full exploitation: any user who clones/fetches from an attacker-hosted git remote with a hostname starting with `github.` will trigger the misclassification automatically the first time Git needs credentials for that remote (e.g. an HTTPS URL requiring auth). No unusual user action beyond adding/cloning the remote is needed; the credential helper invocation happens as part of normal Git operation.

### Recommendation
Replace the substring-based heuristic in `isGitHubHost` (and the equivalent check duplicated in `getEndpointKind`) with an allowlist-based or suffix-anchored check (e.g., match `hostname === 'github.<x>'` only against a small set of known top-level enterprise naming conventions, or require confirmation via the existing `/meta` HTTP probe that already checks for the `x-github-request-id` header) before ever classifying an unknown host as `'enterprise'`. At minimum, do not treat "startswith/contains `github.`" as sufficient signal to route into the enterprise/OAuth sign-in UX; fall back to the generic credential prompt unless the meta-endpoint verification succeeds.

### Proof of Concept
1. Attacker registers `github.attacker.com` and stands up a git server (or GitHub-like clone) there requiring HTTP authentication.
2. Victim adds/clones a repository with remote `https://github.attacker.com/foo/bar.git` in GitHub Desktop.
3. Git invokes the credential helper trampoline (`git credential get`) for that URL; `getEndpointKind` calls `isGitHubHost('https://github.attacker.com')`.
4. `hostname = 'github.attacker.com'` matches `/(^|\.)(github)\./` at position 0, so `isGitHubHost` returns `true` and `getEndpointKind` returns `'enterprise'`.
5. Since no existing account has `endpoint === apiEndpoint`, `getCredential` calls `ui.promptForGitHubSignIn('https://github.attacker.com')`, which invokes `beginEnterpriseSignIn` and shows the enterprise/OAuth sign-in dialog for the attacker's origin instead of the generic username/password prompt a non-GitHub host should get. [6](#0-5) [1](#0-0)

### Citations

**File:** app/src/lib/api.ts (L2451-2454)
```typescript
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
