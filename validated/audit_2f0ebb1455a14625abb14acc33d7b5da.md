## Title
Spoofable `WWW-Authenticate` header lets an attacker-controlled remote/proxy trick Desktop's credential trampoline into treating any host as a trusted GitHub Enterprise endpoint - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

## Summary
Like the `SmoothlyPool` bug, where an on-chain contract silently trusted off-chain accounting instead of verifying it on-chain, GitHub Desktop's credential trampoline silently trusts an **unverified, server-supplied signal** (the `WWW-Authenticate` header Git forwards from an HTTP response) to decide whether a completely arbitrary host should be treated as a GitHub/GitHub Enterprise endpoint. Because this classification is never independently verified, a malicious git remote or a man-in-the-middle proxy can flip that classification and cause Desktop to solicit GitHub sign-in against the attacker's host.

## Finding Description
When Git needs credentials it invokes Desktop's askpass/credential-helper trampoline, forwarding whatever `WWW-Authenticate` headers the remote server returned as `wwwauth[]=...` fields. `getEndpointKind` uses this attacker-supplied string, without any additional verification, to decide the endpoint's trust class: [1](#0-0) 

Specifically:
```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
``` [2](#0-1) 

This "enterprise" verdict then feeds `getCredential`, which — if no existing account matches the (attacker) endpoint — prompts the user to sign in to GitHub against that exact endpoint: [3](#0-2) 

The broken invariant: **"this host claims to be GitHub" is derived purely from a header the remote host itself controls**, not from anything Desktop independently validated (e.g., TLS certificate identity, a known GitHub API response, or user-entered enterprise URL). Contrast this with the `dotCom`/`ghe.com` checks a few lines above, which are based on Desktop's own hard-coded knowledge — the header-based fallback bypasses that safety net entirely.

Attacker control: a user adding/fetching from any attacker-supplied git remote URL (e.g. from a cloned repo's `.gitmodules`, a forked PR remote, or a corporate/public proxy performing MITM) can serve an HTTP 401 response with `WWW-Authenticate: Basic realm="GitHub"` for the git-over-HTTP endpoint. This satisfies the exact "attacker controls...a git remote/proxy response" impact category.

## Impact Explanation
Once `getEndpointKind` returns `'enterprise'` for the attacker's host, `getCredential` falls into the sign-in prompt branch (since no legitimate account matches that made-up "GitHub Enterprise" endpoint), causing Desktop to display a GitHub sign-in dialog whose target endpoint is the attacker-controlled URL rather than a genuine GitHub instance the user vetted. Because the user did not type this URL themselves (it was silently inferred from header trust), they may not notice they're authenticating against a hostile server, leading to credential/token exfiltration and unauthorized "GitHub Enterprise" account binding inside Desktop's `AccountsStore`. This mirrors the smart-contract bug's essential shape: a critical trust/accounting decision is made from unverified external input instead of a verified on-chain (here: locally-verified) source.

## Likelihood Explanation
Low-to-moderate: it requires the victim to add or interact with a repository/remote pointing at an attacker-controlled or MITM'd HTTP(S) endpoint (e.g. cloning a malicious repo with a crafted submodule/remote, or being on a hostile network/proxy) — no local access, no prior malware, and no unnatural user steps beyond normal Desktop usage (adding a remote, fetching, pushing). This satisfies the "Valid Impact" bar of attacker controlling a git remote/proxy response.

## Recommendation
Do not classify a host as `'enterprise'`/GitHub-trusted based solely on a server-supplied `WWW-Authenticate` realm string. If this heuristic must be kept as a UX convenience, treat it only as a hint that triggers an explicit, clearly-labeled user confirmation ("This site claims to be a GitHub Enterprise server — do you want to add it as one?") rather than silently feeding it into the same trusted code path used for `isDotCom`/`isGHE` checks, and never auto-populate/trigger the GitHub sign-in flow against a header-derived, unverified endpoint.

## Proof of Concept
1. Set up an HTTP server (or MITM proxy) that responds to Git's credential probe (`GET /info/refs?service=git-upload-pack`) with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone or add a remote pointing at `http://attacker.example/repo.git` and trigger a fetch/push.
3. Git invokes the Desktop credential trampoline; `command.stdin` includes `wwwauth[]=Basic realm="GitHub"` along with `host=attacker.example`.
4. `getEndpointKind` matches the `realm="GitHub"` substring and returns `'enterprise'` [4](#0-3) .
5. `getCredential` finds no account for `apiEndpoint` derived from `attacker.example` and calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint = http://attacker.example` [5](#0-4) , surfacing a GitHub sign-in dialog scoped to the attacker's host.

Note: I was unable to inspect the full implementation of `trampoline-ui-helper.ts`'s `promptForGitHubSignIn` (not indexed in this search), so I cannot confirm the exact wording/UI shown to the user in that final dialog — a Devin session with full repo access would be needed to verify precisely how the sign-in prompt is rendered and whether it discloses the untrusted origin to the user before requesting credentials.

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
