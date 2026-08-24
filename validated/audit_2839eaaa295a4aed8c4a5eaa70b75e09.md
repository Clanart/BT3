### Title
Spoofed `WWW-Authenticate` header from a malicious git remote forces GitHub Desktop into a GitHub Enterprise sign-in flow for an untrusted host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The root cause pattern in the smart-contract report is that a security-critical decision (moving another user's assets) is made based on attacker-supplied, unauthenticated input (`from`), with no check that the caller is actually authorized for that value. The Desktop analog is `getEndpointKind()` in the git credential-helper trampoline, which decides whether a git remote should be treated as an authenticated "enterprise" (GitHub) endpoint based purely on the content of a `WWW-Authenticate` header that the *remote git server itself* returns during authentication — a value fully controlled by whoever operates that remote.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes the Desktop credential helper (`getCredential`), which calls `getEndpointKind(cred, store)` to classify the endpoint before deciding how to source credentials [1](#0-0) .

Inside `getEndpointKind`, before any real GitHub-hood verification (`isGitHubHost`) is attempted, the code trusts any `wwwauth[...]` entry captured from the remote server's HTTP response and classifies the endpoint as `'enterprise'` solely because the header text contains `realm="GitHub"`: [2](#0-1) 

This header is not a value Desktop generates or validates against any known GitHub instance — it is copied verbatim from whatever the remote HTTP endpoint (attacker-controlled server, MITM proxy, or malicious self-hosted "git" server the user added as a remote) chooses to send back. Once classified as `'enterprise'`, back in `getCredential`, if no existing account matches the endpoint's origin, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, driving the user into a "Sign in to GitHub Enterprise"-style flow pointed at the attacker's arbitrary URL: [3](#0-2) 

The only real host-verification path (`isGitHubHost(endpoint)`) is reached solely as a fallback when no `wwwauth[...]` header is present, meaning a spoofed header short-circuits the intended safety check entirely: [4](#0-3) 

This mirrors the contract bug's broken invariant: a privileged branch ("this is a trusted GitHub endpoint, trigger sign-in / credential flow") is entered based on unauthenticated attacker-supplied data (`from` in the contract; `WWW-Authenticate` header in Desktop) instead of a value the application itself controls or verifies.

### Impact Explanation
An attacker who controls a git remote endpoint (e.g., the user adds/clones from a malicious self-hosted "git server," or a MITM/rogue proxy intercepts the HTTPS git transaction) can force GitHub Desktop to treat their arbitrary host as a legitimate GitHub Enterprise endpoint and trigger a "GitHub sign-in" prompt for it. This can be leveraged to phish the user's GitHub credentials/OAuth flow under the guise of Desktop's own trusted sign-in UI, or to bind an account/endpoint pairing to a host that was never verified as a real GitHub instance — matching the "unauthorized OAuth or account binding" impact category.

### Likelihood Explanation
Exploitation only requires the attacker to control the HTTP response of a git remote the victim interacts with (adding a remote, cloning, fetching, or pushing) — no local access, no prior credential leak, and no unnatural user steps beyond normal git operations Desktop already performs automatically when fetching/authenticating. The header value is entirely attacker-controlled and requires no complex bypass, only returning a crafted `WWW-Authenticate: Basic realm="GitHub"` on the 401 challenge.

### Recommendation
Do not classify an endpoint as `'enterprise'` based solely on a server-supplied `WWW-Authenticate` realm string. At minimum, always corroborate the header hint with an authoritative check (`isGitHubHost(endpoint)`, or matching against a known/pinned GHE account origin) before entering the GitHub-specific credential/sign-in code path, and never use unverified header content to short-circuit host verification.

### Proof of Concept
1. Attacker hosts a git server (or sits as a MITM/rogue proxy) at `https://attacker.example.com/some-repo.git`.
2. Victim adds this URL as a remote in GitHub Desktop (or clones it) and performs a fetch/push that requires auth.
3. The attacker's server responds to the initial unauthenticated request with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git captures this into `wwwauth[0]=Basic realm="GitHub"` and forwards it to Desktop's credential helper via the trampoline.
5. `getEndpointKind` sees the `wwwauth[` entry containing `realm="GitHub"` and immediately returns `'enterprise'`, skipping the `isGitHubHost` network verification entirely (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`).
6. Since no account exists for `attacker.example.com`, `getCredential` invokes `ui.promptForGitHubSignIn(endpoint)`, presenting the victim with what looks like Desktop's trusted GitHub Enterprise sign-in dialog pointed at the attacker's host (`app/src/lib/trampoline/trampoline-credential-helper.ts:109-125`).

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-135)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-179)
```typescript

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
