## Analysis

The Sherlock report's broken invariant is: **an attacker-controlled entity's self-reported data (the callee's return value / behavior) is treated as authoritative for a security decision**, instead of being verified through a trustworthy, independent channel.

The closest analog in this codebase is in the Git credential-helper trampoline's host-classification logic in `app/src/lib/trampoline/trampoline-credential-helper.ts`.

### Title
Attacker-controlled `WWW-Authenticate` realm is trusted as proof of a GitHub host, short-circuiting the real host verification - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
When Git negotiates authentication with a remote, the server can return a `WWW-Authenticate` header, which Git forwards to Desktop's credential helper as `wwwauth[...]` fields. `getEndpointKind()` inspects this header's `realm=` value and, if it contains `"GitHub"`, immediately classifies the endpoint as `'enterprise'` — before ever calling the actual verification function `isGitHubHost()`. This header is fully attacker-controlled content coming from the remote server being authenticated against, analogous to a malicious ERC20 token forging a "successful" `transferFrom` result: the callee (the remote server) supplies data that is trusted at face value to drive a privileged decision.

### Finding Description [1](#0-0) 

```
const getEndpointKind = async (cred: Credential, store: Store) => {
  ...
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
  ...
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```

The comment itself documents the design flaw: the `wwwauth[]` header is used as a "happy-path" **specifically to avoid** making the real verification request (`isGitHubHost`). This means the one code path that actually validates whether a server is really running GitHub Enterprise is bypassed entirely whenever the server chooses to emit `WWW-Authenticate: realm="GitHub"` on a 401. Since this header value originates from the HTTP response of the remote Git server the user is connecting to (which can be any arbitrary `git clone`/`fetch`/`push` URL the user or a deep link supplies), a malicious server fully controls this classification with zero cryptographic or out-of-band verification — exactly like a malicious ERC20 contract fabricating a "success" return value that the caller (`CardTopupPermit`/`_processTopup`) accepts without any independent check.

Downstream, `getCredential()` uses this classification to decide the authentication flow: [2](#0-1) 

```
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)
  if (ghCred) return ghCred

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()
  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)
    ...
    return credWithAccount(cred, account)
  }
  ...
}
```

Because `endpointKind` is `'enterprise'` (attacker-forged), and no account is yet stored for `apiEndpoint` (the attacker's own domain), Desktop presents the user with the **GitHub sign-in flow** for that endpoint instead of the generic-git-credential prompt. This routes what should be a plain (and much less sensitive) Git Basic/negotiate authentication for an arbitrary remote into GitHub's OAuth/device-flow sign-in UX bound to a domain the attacker fully controls — a decision that should only ever be made after a real, verifiable check (`isGitHubHost`) that this code path deliberately skips whenever the forged header is present.

### Impact Explanation
An attacker who controls the git server a victim is connecting to (a plain `git clone <attacker-url>`, a bookmarked remote, or a `x-github-client://`-style deep link that triggers a fetch/clone against a URL of the attacker's choosing) can force Desktop to classify their arbitrary host as a genuine GitHub Enterprise endpoint by returning a single spoofed HTTP header. This:
- Corrupts the value `endpointKind` (and therefore the authentication UX/flow chosen) — trusted purely from attacker-controlled data with the verifying call (`isGitHubHost`) skipped.
- Can be used to lure the victim into initiating a GitHub sign-in/OAuth flow scoped to the attacker's chosen endpoint, presenting it with GitHub-Enterprise-branded trust cues Desktop itself generates, increasing the credibility of a credential-phishing attempt that the victim did not choose to initiate (Desktop initiated it automatically based on the forged header).
- Falls squarely in the "unauthorized OAuth or account binding" / "credential exfiltration" category described as valid impact, since it is the attacker's remote/proxy response driving Desktop's trust decision.

### Likelihood Explanation
Likelihood is moderate-to-high for the specific trigger (any attacker-controlled git remote can add this header to a 401 response with no special conditions), but the actual harm depends on whether the victim proceeds through the resulting sign-in prompt. No local access, malware, or leaked credentials are required — only that the user attempt to authenticate against an attacker-supplied Git URL, which is a normal, expected Desktop interaction (adding a remote, cloning, or following a deep link).

### Recommendation
Do not treat the `wwwauth[]` `realm=` value as sufficient proof that a host is a genuine GitHub/GitHub Enterprise instance. At minimum:
- Remove the "happy path" short-circuit and always fall through to the verified `isGitHubHost()` check (or perform it in parallel and require it to agree) before classifying an unknown endpoint as `'enterprise'`.
- If the header hint is kept as an optimization, only use it to *skip a redundant confirmatory network check* for endpoints that are already known/trusted, never to bypass verification for previously-unseen endpoints prior to prompting the user for GitHub sign-in.

### Proof of Concept
1. Attacker serves a git-over-HTTP endpoint at `https://evil.example.com/repo.git` that:
   - Responds `401 Unauthorized` to the initial git HTTP request with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote / clones it / opens a deep link that triggers a fetch against it in GitHub Desktop.
3. Git forwards the captured `WWW-Authenticate` header to Desktop's credential helper trampoline as a `wwwauth[...]=...realm="GitHub"...` field in the `get` credential request.
4. `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'` without ever calling `isGitHubHost('https://evil.example.com')` — the real verification is never performed. [3](#0-2) 
5. Since no account exists yet for `evil.example.com`, `getCredential()` calls `ui.promptForGitHubSignIn('https://evil.example.com')`, presenting the victim with Desktop's built-in "Sign in to GitHub Enterprise" flow for a host that was never actually verified to be running GitHub Enterprise. [4](#0-3) 

Note: I was unable to fully trace `promptForGitHubSignIn`'s implementation in `trampoline-ui-helper.ts` or the exact OAuth/token-exchange sequence it triggers (which endpoint receives the resulting token) before running out of investigation budget — this would need to be confirmed to establish whether the resulting OAuth token is sent to the attacker's endpoint directly or only used against api.github.com, which would change the severity from "phishing-enabling UX bypass" to "direct token exfiltration."

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
