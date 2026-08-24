## Title
Spoofable `WWW-Authenticate: realm="GitHub"` header lets a malicious remote skip GitHub-host verification and trigger a credential-store prompt for an arbitrary host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

## Summary
The Chainlink report's broken invariant is: an external, attacker-influenceable data field (`updateAt`/price bounds) is trusted without validating it against an independent, authoritative check before being used for a security-relevant decision. The direct Desktop analog is `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts`. When Git performs HTTP authentication against a remote, it forwards any `WWW-Authenticate` response header to Desktop's credential-helper trampoline via `wwwauth[]` fields. Desktop treats the mere presence of the substring `realm="GitHub"` in that header as sufficient proof that the remote is a genuine GitHub/GHE host, short-circuiting the authoritative check (`isGitHubHost()`, an actual API probe) that is otherwise used as the fallback verification.

## Finding Description
`getEndpointKind()` [1](#0-0)  classifies a git remote's host as `'github.com'`, `'ghe.com'`, `'enterprise'`, or `'generic'`. For hosts that don't match known GitHub.com/GHE domain patterns, it falls back to inspecting attacker-controlled response headers: [2](#0-1) 

The comment itself acknowledges this is a "happy-path" heuristic used "without having to resort to making a request ourselves" — i.e., it is explicitly a shortcut around the real validation. Any HTTPS server the user's Git client talks to (a malicious `git remote`, a compromised/attacker-controlled HTTP proxy sitting in front of a clone/fetch/push, or a redirect target) can simply respond to the Git HTTP auth challenge with a header such as `WWW-Authenticate: Basic realm="GitHub"` and Desktop will classify it as `'enterprise'` without ever calling `isGitHubHost()` to confirm the host is actually a GitHub Enterprise instance.

This classification then feeds `getCredential()`: [3](#0-2) 

Because the spoofed host has no existing account entry, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, presenting the user a "Sign in to GitHub" style prompt bound to the attacker's endpoint rather than the generic-git-credential prompt that a non-GitHub host would normally get. If the user completes that sign-in (OAuth or PAT), the resulting `Account` is bound to the attacker-supplied `endpoint` string. Because `findGitHubTrampolineAccount()` matches purely on endpoint host [4](#0-3) , subsequent Git operations against that same malicious host will have the real GitHub token supplied to it automatically as credentials — this is a credential-exfiltration / unauthorized-account-binding primitive, not merely a UI nuisance.

Contrast this with the legitimate fallback path a few lines below, which performs an actual network-based verification (`isGitHubHost(endpoint)`) before granting `'enterprise'` classification — that authoritative check exists precisely because header content alone isn't trustworthy, yet it is bypassed whenever the `wwwauth` heuristic matches first.

## Impact Explanation
This fits the "attacker controls ... a git remote/proxy response" and "credential/token exfiltration, unauthorized OAuth or account binding" categories explicitly listed as valid impact. A user who clones or fetches from an attacker-controlled or MITM'd HTTP git endpoint (no admin rights, no local access, no leaked credentials required — just an untrusted remote/proxy in the normal clone/fetch/push flow) can be steered into a GitHub-branded sign-in prompt for a host that isn't GitHub, and have their real GitHub OAuth token subsequently associated with and sent to that attacker endpoint on future operations.

## Likelihood Explanation
Exploitation requires the victim to add/use a remote pointing at (or be redirected/proxied through) an attacker-controlled HTTPS endpoint and to accept the resulting sign-in prompt — a plausible but not fully passive scenario, since it depends on the server responding to Git's Basic-auth-style challenge and the user completing sign-in. There is no requirement for special git config, local file access, or pre-existing malware, matching the "unprivileged" bar in the task's Valid Impact definition. Overall likelihood is Medium: the header-spoofing step is trivial for any attacker-run/compromised HTTPS server, but the final compromise depends on user interaction with the sign-in prompt.

## Recommendation
Do not use the `WWW-Authenticate` realm string as sole proof of GitHub identity. Either:
- Always perform the authoritative `isGitHubHost()` check (or equivalent, e.g. verifying a known GitHub-specific API response/certificate) before classifying an unknown host as `'enterprise'`, regardless of header content, or
- Treat the `wwwauth` heuristic only as a hint to *skip an unnecessary network round trip when there is already independent corroboration* (e.g., an existing matching account), never as sufficient grounds by itself to present a GitHub-branded credential prompt for a previously-unknown host.

## Proof of Concept
1. Stand up an HTTPS server (or MITM proxy) at `https://evil.example.com` that responds to Git's unauthenticated request for `info/refs?service=git-upload-pack` with HTTP 401 and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone/add remote `https://evil.example.com/attacker/repo.git` and trigger a fetch.
3. Git forwards the `WWW-Authenticate` header as `wwwauth[0]=...realm="GitHub"...` to the credential-helper trampoline; `getEndpointKind()` returns `'enterprise'` without calling `isGitHubHost()`.
4. Since no account exists for `evil.example.com`, Desktop shows `ui.promptForGitHubSignIn('https://evil.example.com')` — a GitHub-branded sign-in flow bound to the attacker's host.
5. If the user signs in, the resulting account/token is bound to `evil.example.com`; subsequent git operations against that host will automatically present the real GitHub token as Basic-auth credentials to the attacker's server.

Note: I could not directly inspect `trampoline-ui-helper.ts`'s `promptForGitHubSignIn` implementation or `isGitHubHost()` in `api.ts` in this session (index limits), so the exact wording/branding of the sign-in dialog and the precise contents of the authoritative `isGitHubHost` check are unverified — a Devin session with full file access would be needed to confirm the exact dialog text and whether any additional host confirmation is shown to the user before token issuance.

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
