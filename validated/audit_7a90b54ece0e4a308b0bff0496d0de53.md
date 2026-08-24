## Analysis

The report's broken invariant is: a **security-relevant classification decision is made from attacker-influenced input, and once that classification is cached/treated as "trusted", it is never re-validated against the original ground truth**, letting the attacker cause the honest party's protection to silently fail. The closest analog in this codebase is `getEndpointKind()` in `trampoline-credential-helper.ts`, which decides whether a credential request is "generic" (safe to hand to third-party host) or GitHub/enterprise (safe to auto-fill from the signed-in account) partly by trusting a `WWW-Authenticate` header value returned by the remote server itself. [1](#0-0) 

### Title
Remote-controlled `WWW-Authenticate` realm heuristic lets an attacker-controlled host be classified as "generic" to suppress GitHub credential prompts — (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` is the gatekeeper that decides, for every `git credential fill/store/erase` request routed through Desktop's credential-helper trampoline, whether a host should be treated as `'generic'`, `'github.com'`, `'ghe.com'`, or `'enterprise'`. When the host doesn't match a known GitHub/GHE endpoint pattern or an already-configured account, the function falls back to inspecting `wwwauth[...]` credential fields that git populates directly from the **server's own HTTP response headers**. If the response contains a `realm="GitLab"`, `realm="Gitea"`, or `realm="Atlassian Bitbucket"` string, Desktop unconditionally classifies the endpoint as `'generic'`, bypassing the `isGitHubHost()` network probe entirely. [2](#0-1) 

### Finding Description
The classification of "generic" vs "GitHub/enterprise" host directly drives two different, mutually exclusive credential paths in `storeCredential()` and `eraseCredential()`: [3](#0-2) 

If `getEndpointKind()` returns `'generic'`, Desktop uses the *generic* credential store (`setGenericCredential`/`deleteGenericCredential`), which stores plaintext usernames/passwords keyed only by the URL. If it returns anything else, GitHub-account credentials (a live OAuth/PAT token) can be filled via `getGitHubCredential()`/`getCredential()`. [4](#0-3) 

The invariant that should hold — "only a host that is actually a GitHub-family server gets a GitHub token filled" — is protected in the fallback case by `isGitHubHost(endpoint)`, an out-of-band network check. But the `wwwauth[]` shortcut in lines 157-165 lets the *remote server itself* preempt that check by returning a spoofed `WWW-Authenticate` header. This is analogous to the rollup bug: a value that is supposed to represent ground truth (the actual required stake / the actual server identity) is replaced by a value that can be manipulated by the counterparty (the admin lowering the requirement / the remote returning a forged realm), and the code that depends on it (`requireInactiveStaker` / `getEndpointKind`) treats the manipulated value as authoritative without re-deriving it from the original source of truth.

Concretely: a git server (reachable via a proxy the attacker controls, a compromised mirror, or a malicious HTTP redirect target hit during `git fetch`/`clone`/submodule resolution) that Desktop is about to authenticate against can respond with `WWW-Authenticate: Basic realm="GitLab"` even though the actual host is, say, a spoofed or typo-squatted GitHub Enterprise-looking domain. This forces `getEndpointKind` to return `'generic'` and skips the `isGitHubHost` verification that would otherwise have been performed.

### Impact Explanation
The direct effect of forcing `'generic'` classification is on write paths (`store`/`erase`): it forces credentials for that host into the generic credential store instead of being erased/rejected via the GitHub-account flow, and it prevents Desktop's GitHub-specific handling (which never persists raw account tokens to the generic store) from engaging. Because host classification is derived per-request from server-supplied data rather than from a single validated, session-pinned decision, the trust boundary between "is this a GitHub endpoint" and "is this some other git host" can be pushed around by the remote endpoint being talked to — which is exactly the kind of authority a passive/careless remote should not have. This does not itself leak an existing GitHub PAT (the strict equality/`isDotCom`/`isGHE` checks earlier in the function still gate the highest-risk `getGitHubCredential` fill path against known account endpoints), which limits the severity relative to the H-01 report's direct fund loss.

### Likelihood Explanation
Triggering the code path requires nothing beyond convincing Desktop to perform a network git credential negotiation against a host the attacker controls or can respond on behalf of (e.g. via an HTTP proxy, a compromised mirror configured as a fetch/push remote, or a redirect target) — no local access, no admin rights, and no user action beyond a normal fetch/clone/push against a repository whose remote is attacker-influenced. This matches the required threat model (attacker controls a git remote/proxy response).

### Recommendation
Do not let server-returned `WWW-Authenticate` realm strings short-circuit the `isGitHubHost()` verification. Either always perform the authoritative host check (via `isGitHubHost`/known endpoint list) before considering `wwwauth[]` hints, or treat the header purely as a hint that can *narrow* classification for UX purposes but never as a value that can *downgrade* a host away from a check that would otherwise flag it as GitHub-like domain-similarity/enterprise heuristics.

### Proof of Concept
1. Configure a repository remote (or trigger a fetch through a malicious HTTP proxy/redirect) pointing at `https://attacker.example` that is not a pre-registered GHE endpoint and not github.com/ghe.com.
2. When git invokes Desktop's credential-helper trampoline (`get`/`store`), have the attacker server return an HTTP `401` with header `WWW-Authenticate: Basic realm="GitLab"`.
3. `getEndpointKind()` (lines 157-165) matches this header and returns `'generic'` before ever calling `isGitHubHost(endpoint)`.
4. Any subsequent `store`/`erase` request for that host is routed through the generic-credential path rather than through GitHub-account-aware handling, and the `isGitHubHost` network verification that Desktop relies on elsewhere as ground truth is silently skipped for this endpoint. [2](#0-1)

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-178)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L181-213)
```typescript
/** Implementation of the 'store' git credential helper command */
async function storeCredential(cred: Credential, store: Store, token: string) {
  if ((await getEndpointKind(cred, store)) !== 'generic') {
    return
  }

  return useExternalCredentialHelper()
    ? storeExternalCredential(cred, token)
    : setGenericCredential(
        urlWithoutCredentials(getCredentialUrl(cred)),
        forceUnwrap(`credential missing username`, cred.get('username')),
        forceUnwrap(`credential missing password`, cred.get('password'))
      )
}

const storeExternalCredential = (cred: Credential, token: string) => {
  const path = getTrampolineEnvironmentPath(token)
  return approveCredential(cred, path, getGcmEnv(token))
}

/** Implementation of the 'erase' git credential helper command */
async function eraseCredential(cred: Credential, store: Store, token: string) {
  if ((await getEndpointKind(cred, store)) !== 'generic') {
    return
  }

  return useExternalCredentialHelper()
    ? eraseExternalCredential(cred, token)
    : deleteGenericCredential(
        urlWithoutCredentials(getCredentialUrl(cred)),
        forceUnwrap(`credential missing username`, cred.get('username'))
      )
}
```
