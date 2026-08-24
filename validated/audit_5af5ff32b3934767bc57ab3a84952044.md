## Title
GitHub host classification for the git credential helper trusts an attacker-controlled `WWW-Authenticate` realm string, allowing a malicious/MITM HTTPS remote to be classified as "generic" and receive GitHub OAuth tokens via the fallback prompt/store path - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` decides whether a git remote host is treated as `github.com`/`enterprise` (credentials handled only internally, never persisted generically) or `generic` (credentials get filled/stored/erased through the generic credential store, or handed to an external credential helper). Part of that decision is driven by the `wwwauth[...]` header value that git captures from the **remote server's own HTTP response** and forwards verbatim into the credential helper's stdin.

### Finding Description
`getEndpointKind` is only reached once the hostname-based checks (`isGist`, `isDotCom`, `isGHE`) fail, i.e. for arbitrary non-`*.github.com`/`*.ghe.com` hosts [1](#0-0) . It then inspects the `wwwauth[]` credential fields that were populated from the server's `WWW-Authenticate` response header:

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
``` [2](#0-1) 

This value is fully attacker-controlled: any HTTPS server the user's git operation talks to (a malicious/compromised remote, a corporate proxy, or a MITM on an insecure network) can send an arbitrary `WWW-Authenticate: Basic realm="GitHub"` header for any hostname, causing Desktop to classify a completely unrelated host as `enterprise`. Only after this heuristic fails does the code fall back to `isGitHubHost(endpoint)`, which performs an actual network probe [3](#0-2) .

The classification decision feeds directly into `getCredential`:
```
if (endpointKind !== 'generic' && !accounts.some(a => a.endpoint === apiEndpoint)) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
  ...
  return credWithAccount(cred, account)
}
``` [4](#0-3) 

If an attacker-controlled host spoofs `realm="GitHub"`, Desktop will treat it as an "enterprise" GitHub host and, when no existing account matches that endpoint, prompt the user with `ui.promptForGitHubSignIn(endpoint)` — a "Sign in to GitHub" dialog for a host the user never intended to authenticate to. If the user (reasonably believing this is a legitimate GitHub sign-in prompt triggered by their own clone/fetch) completes it, the resulting GitHub account credential (`credWithAccount`) is handed back to git as the credential for that attacker-controlled endpoint, i.e. the OAuth token is sent to the attacker's server. This differs from the report's exact mechanic (a missing precondition check causing a revert) but is the same bug class: the guard responsible for gating a security-sensitive branch is being driven by data that should never have been trusted without an independent check, and the fallback ordering means the untrusted heuristic can short-circuit the safe path before the trustworthy `isGitHubHost()` network check ever runs.

### Impact Explanation
A crafted git remote/proxy response can misdirect GitHub Desktop's credential helper into presenting a GitHub sign-in prompt for an arbitrary host and, if completed, exfiltrate the resulting OAuth token to that host. This matches the "attacker controls a git remote/proxy response... resulting in credential/token exfiltration" impact category.

### Likelihood Explanation
Exploitation requires the user to add/clone from or fetch against an attacker-controlled or MITM'd HTTPS remote, and then to complete a sign-in prompt they weren't expecting — this is a real but not-trivial user-interaction requirement (not local access, malware, or leaked credentials). The `realm="GitHub"` string is fully attacker-supplied in the HTTP response, requiring no special privileges to produce, so likelihood centers on whether the resulting UI prompt would appear legitimate/convincing enough to a typical user.

### Recommendation
Do not let the `wwwauth[]` realm heuristic promote a host to `enterprise`/`github.com` classification on its own. Either drop the header-based fast path entirely in favor of always confirming via `isGitHubHost(endpoint)` (an authoritative check), or treat the header only as a hint to *skip* the generic path when it also matches an already-known/allow-listed account endpoint — never as sufficient justification to trigger the GitHub sign-in prompt.

### Proof of Concept
1. Attacker hosts an HTTPS git server (or MITMs a plaintext connection) at `https://evil.example.com/repo.git`.
2. Victim adds/clones this repository in GitHub Desktop.
3. During the git HTTP request, the attacker's server responds with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git captures this into the `wwwauth[]` field and forwards it via the credential helper trampoline to `getCredential()` → `getEndpointKind()`, which returns `'enterprise'` for `evil.example.com` [5](#0-4) .
5. Since no stored account matches `evil.example.com`, Desktop calls `ui.promptForGitHubSignIn(endpoint)` [6](#0-5) , showing a GitHub sign-in dialog.
6. If the victim signs in, the resulting account credential is returned to git for use against `evil.example.com`, sending the OAuth token to the attacker.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-151)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L167-178)
```typescript
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
