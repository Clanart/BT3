### Title
Attacker-controlled Git host can spoof GitHub/Enterprise classification, triggering a false "Sign in to GitHub" OAuth prompt instead of a generic credential prompt - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
GitHub Desktop's credential helper decides whether an unknown git remote host should be treated as `'github.com'`, `'enterprise'`, or `'generic'` using heuristics that trust attacker-influenced signals: an unauthenticated `WWW-Authenticate` realm string returned by the remote server, and a hostname substring/regex match. An attacker who controls the git server being cloned/fetched from (or a proxy in front of it) can manipulate these signals to make Desktop classify their host as a trusted GitHub/GitHub Enterprise endpoint, changing the credential flow from a generic username/password prompt to a GitHub-branded sign-in/OAuth flow pointed at attacker infrastructure.

### Finding Description
`getEndpointKind` in the trampoline credential helper decides how to treat a host based on unauthenticated attacker-observable data: [1](#0-0) 

Two of its branches accept externally-supplied, unverified signals as ground truth:

1. It parses `WWW-Authenticate` headers captured by git during a failed auth attempt against the remote, and if any header value contains `realm="GitHub"`, it classifies the endpoint as `'enterprise'` — with no cryptographic or out-of-band verification that the responding server is actually a GitHub Enterprise Server: [2](#0-1) 

2. As a fallback it calls `isGitHubHost`, which classifies a hostname as GitHub-like via a loose regex, `/(^|\.)(github)\./`, matching any hostname containing a `.github.` label — trivially satisfiable by an attacker who registers/controls a subdomain such as `x.github.example.com`: [3](#0-2) 

The root cause mirrors the reference report's broken invariant: the code assumes a fixed 1:1 relationship ("this response/hostname pattern implies this host is a real GitHub identity") without validating that assumption against an authoritative source, the same way the Vyper `CONTEXT`/`extract_price` code assumed `<quote-token> == USD` without checking the peg held. Here, "peg" is the assumption that `realm="GitHub"` or a `.github.` hostname substring implies the host is a genuine GitHub/GHES instance.

Once classified as `'enterprise'` or `'github.com'`, `getCredential` skips the generic username/password credential path entirely and, if no matching account exists, invokes the GitHub-specific sign-in prompt instead of the generic credential prompt: [4](#0-3) 

### Impact Explanation
This does not directly disclose an existing token (the strict account-matching by URL origin in `findGitHubTrampolineAccount` still prevents cross-host reuse of stored GitHub credentials), but it does let an attacker who merely controls the git remote/host being fetched cause Desktop to present a "Sign in to GitHub Enterprise" experience for a host the user never configured as an Enterprise account. Because GHE sign-in exchanges OAuth codes against `<endpoint>/login/oauth/authorize` and `<endpoint>/login/oauth/access_token` on the attacker's own domain (the "enterprise API URL" is derived directly from the attacker's hostname), the attacker's server fully controls what the user sees during that flow. This elevates a generic-credential-only surface into an OAuth/enterprise-branded surface, increasing the plausibility of credential/token capture and unauthorized account binding, since the user is led to believe they are authenticating to a legitimate GitHub Enterprise instance inside Desktop's own trusted chrome.

### Likelihood Explanation
Requires only that the victim performs an ordinary git operation (clone/fetch/push over HTTPS) against a repository hosted by the attacker — no local access, no leaked credentials, no admin rights. Satisfying the `.github.` hostname substring is a matter of registering a subdomain; satisfying the `WWW-Authenticate: realm="GitHub"` path requires only controlling the HTTP response to git's unauthenticated probe request, which is trivial for anyone operating the server. This fits the "attacker controls a git remote/proxy response" precondition explicitly listed as in-scope.

### Recommendation
Do not classify a remote as GitHub/GitHub Enterprise based solely on unauthenticated, attacker-suppliable signals (`WWW-Authenticate` realm text, hostname substring matches). Require a positive, verifiable signal (e.g., a successful TLS-protected request returning the `x-github-request-id` header via `isGitHubHost`'s live-check branch, which already exists) before elevating the credential flow beyond a generic prompt, and treat the heuristic branches as advisory only — never sufficient on their own to trigger the GitHub-branded OAuth/enterprise sign-in path.

### Proof of Concept
1. Attacker registers/controls a host such as `code.github.evilcorp.example` (or any HTTPS git server) and configures it to answer unauthenticated Basic-auth git requests with `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim runs `git clone https://code.github.evilcorp.example/attacker/repo` (or adds it as a remote) via GitHub Desktop.
3. Git's credential helper invokes Desktop's trampoline `get` command; `getEndpointKind` sees the `wwwauth[...]` header value containing `realm="GitHub"` and returns `'enterprise'` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`), or alternatively the hostname regex match in `isGitHubHost` (`app/src/lib/api.ts:2452-2454`) returns `true`.
4. Since no existing account matches this endpoint, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` instead of the generic username/password dialog (`app/src/lib/trampoline/trampoline-credential-helper.ts:109-124`), presenting the user with a GitHub/Enterprise-branded authentication flow that ultimately makes requests to the attacker-controlled domain.

Note: I was unable to fully inspect the UI implementation of `promptForGitHubSignIn` in `trampoline-ui-helper.ts` (only its declaration was located, not the rendered dialog/OAuth flow contents), so I cannot confirm the exact wording/branding shown to the user or whether any additional confirmation step mitigates this before it reaches OAuth-code exchange. This should be verified before treating the impact severity as final.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-130)
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
