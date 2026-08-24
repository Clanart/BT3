## Analysis

This report describes a class of bug where a security-relevant check (verifying a token/asset type before treating it a certain way) is applied consistently in most of the codebase but skipped in one code path, causing that path to behave incorrectly on attacker-influenced input.

The closest analog I found in `Annirich/desktop--001` is in the git-credential trampoline handler, specifically `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts`.

### Finding

`getEndpointKind()` classifies a remote host as `'github.com' | 'ghe.com' | 'enterprise' | 'generic'` in order to decide whether Desktop should treat the connecting host as a GitHub endpoint (and prompt the user to sign in with GitHub / potentially reuse stored GitHub credentials for it).

Most branches perform a real verification: `isDotCom()` / `isGHE()` do exact hostname matching, and the final fallback calls `isGitHubHost(endpoint)`, which — per the surrounding comment — "resort[s] to making a request ourselves" to actually confirm the host is GitHub. [1](#0-0) 

But one branch skips that verification entirely: if the credential input contains a `wwwauth[...]` field (captured from the server's `WWW-Authenticate` HTTP header during a git operation) whose value contains `realm="GitHub"`, the code immediately classifies the endpoint as `'enterprise'` — with no independent check that the host is actually a GitHub/GHE instance: [2](#0-1) 

That classification then feeds directly into `getCredential()`, which — for any endpoint classified as non-`'generic'` with no matching stored account — invokes `ui.promptForGitHubSignIn(endpoint)`, surfacing a "Sign in to GitHub" dialog for that attacker-controlled endpoint: [3](#0-2) 

The `WWW-Authenticate` header is emitted by the remote git server (or an HTTP proxy in the path) during a `fetch`/`clone`/`push`, and is fully attacker-controlled content forwarded by git into the credential-helper protocol as a `wwwauth[N]=` line. `getCredentialUrl(cred)` / `getAPIEndpoint(endpoint)` derive the endpoint straight from the remote URL, so any git host — not just GitHub — can trigger this branch simply by responding with `WWW-Authenticate: Basic realm="GitHub"` on a 401.

### Why existing guards don't stop this
Elsewhere in the same function, GitHub-ness is either matched by exact hostname (`isDotCom`, `isGHE`) or actually verified over the network (`isGitHubHost`). The `wwwauth` branch is the sole exception: it trusts a value the remote peer chooses to send, with no cross-check against the real host identity — mirroring exactly the report's pattern of "one path skips a check performed everywhere else in the codebase."

### Impact
An attacker who controls a git remote/proxy a user fetches or clones from can force Desktop to surface a "Sign in to GitHub" (OAuth) prompt scoped to the attacker's own host, misrepresenting it as a GitHub Enterprise endpoint the app has determined to be legitimate. This matches the "unauthorized OAuth or account binding" impact category, since the classification (and thus the decision to initiate a GitHub-branded sign-in flow) is driven purely by attacker-supplied header content rather than verified host identity.

### Caveat
I could not verify the exact implementation of `isGitHubHost()` in `app/src/lib/api.ts` (only its declaration was located via grep, not its body) or the full behavior of `trampolineUIHelper.promptForGitHubSignIn`, so I cannot confirm what happens after the prompt is shown (e.g., whether it always initiates a real GitHub.com OAuth flow, or whether it could be tricked into treating the attacker host as an enterprise endpoint for subsequent token storage). Given index size limits, some file contents may not be indexed — a full Devin session would be needed to inspect `app/src/lib/api.ts` and `app/src/lib/trampoline/trampoline-ui-helper.ts` in full to determine the exact downstream consequences and confirm whether stored credentials could ultimately be bound to or sent toward the attacker-controlled endpoint.

### Title
Attacker-controlled `WWW-Authenticate` header can spoof GitHub endpoint classification in credential helper - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` classifies a git remote as an `'enterprise'` (GitHub) endpoint based solely on the content of an attacker-controlled `WWW-Authenticate` HTTP header (`realm="GitHub"`), bypassing the real hostname/API verification (`isDotCom`, `isGHE`, `isGitHubHost`) used everywhere else in the same function.

### Finding Description [4](#0-3) 
The function checks exact-match helpers first, then falls through to parsing `wwwauth[...]` credential-helper fields (originating from the server's HTTP response header) and trusts a `realm="GitHub"` value without any independent verification, only falling back to the network-verified `isGitHubHost()` call if no such header is present.

### Impact Explanation
Any git server or MITM proxy in the path of a `fetch`/`clone`/`push` operation can send `WWW-Authenticate: Basic realm="GitHub"` to have Desktop treat it as a trusted GitHub/GHE endpoint, which triggers `ui.promptForGitHubSignIn(endpoint)` for that attacker-controlled endpoint — an unauthorized/misleading GitHub sign-in flow bound to a host the app has not actually verified as GitHub.

### Likelihood Explanation
Moderate: requires the user to interact with a malicious or compromised git remote/proxy (no local access, no prior malware, no admin rights needed), which is within the accepted attacker model for this exercise (attacker controls a git remote/proxy response).

### Recommendation
Do not classify a host as GitHub/enterprise based solely on the `WWW-Authenticate` realm string; require it to be corroborated by the same network-based verification (`isGitHubHost`) used in the fallback path, or drop the header-based heuristic entirely.

### Proof of Concept
1. Stand up a git-over-HTTP server (or a MITM proxy for an HTTP(S) remote) that responds to authentication challenges with `WWW-Authenticate: Basic realm="GitHub"`.
2. Have the victim add this server as a remote and perform `fetch`/`push` in GitHub Desktop.
3. Git invokes Desktop's credential helper trampoline; `getCredential` → `getEndpointKind` reads the forwarded `wwwauth[...]="...realm=\"GitHub\"..."` value and returns `'enterprise'` for the attacker's host without ever confirming it is actually GitHub.
4. Because no stored account matches the attacker endpoint, `ui.promptForGitHubSignIn(endpoint)` is invoked for that host, presenting the user with a GitHub-branded sign-in prompt scoped to the attacker's server.

### Citations

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
