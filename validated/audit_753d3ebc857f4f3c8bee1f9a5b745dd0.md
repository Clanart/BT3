### Title
Spoofed `WWW-Authenticate` realm header from an untrusted git remote triggers a GitHub/GHE OAuth sign-in prompt without host verification - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` decides whether a remote host being authenticated against should be treated as `github.com`, `enterprise`, or `generic`. This decision gates whether Desktop shows a GitHub/GHE OAuth sign-in popup (`promptForGitHubSignIn`) for that exact host. One of the classification branches trusts the `wwwauth[]` credential fields that Git populates directly from the server's `WWW-Authenticate` HTTP header, treating any host that returns `realm="GitHub"` as `'enterprise'` — without ever performing the network-based verification (`isGitHubHost`) that the same function uses as a fallback for unknown hosts. [1](#0-0) 

### Finding Description
`getEndpointKind` is invoked by the credential-helper trampoline (`createCredentialHelperTrampolineHandler` → `getCredential`) every time Git needs credentials for an HTTPS remote (fetch/pull/push/clone), i.e. it runs for arbitrary remotes the user adds or clones, including attacker-controlled ones. [2](#0-1) 

The classification logic is:
1. Known GitHub/GHE domains → classified directly.
2. Otherwise, it inspects the `wwwauth[...]` credential fields — which originate from Git capturing the remote server's `WWW-Authenticate` response header — and if any value contains `realm="GitHub"` it is unconditionally classified as `'enterprise'`.
3. Only if none of the earlier heuristics matched does it fall back to an actual network check, `isGitHubHost(endpoint)`. [3](#0-2) 

The broken invariant: the `wwwauth[]` value is **attacker-controlled data** (any HTTP(S) server, or any proxy sitting in front of one, can freely return `WWW-Authenticate: Basic realm="GitHub"` on a 401 challenge). The code treats this untrusted string as authoritative proof of the host's identity and skips the one check (`isGitHubHost`, an actual API probe) designed to authoritatively confirm that the host behaves like GitHub. This mirrors the Vault bug's pattern: a value that is supposed to gate a privileged operation (`endpointKind`/`owner`) can be mutated by an untrusted external actor (`delegatecall` target / attacker's git server) mid-flow, and the code's "safety check" (owner comparison / `isGitHubHost` fallback) is bypassed because the check only runs in one branch and not on the other, attacker-reachable, path.

Once `endpointKind !== 'generic'` and no account exists for that host, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

`promptForGitHubSignIn` then opens Desktop's real GitHub Enterprise OAuth sign-in flow, binding the resulting account to `origin` — which is the attacker's own domain, not github.com: [5](#0-4) 

### Impact Explanation
By simply cloning/adding a remote pointing at an attacker-controlled HTTPS host (or any server, e.g. a compromised corporate proxy or MITM on an internal network) and having that server answer authentication challenges with `WWW-Authenticate: Basic realm="GitHub"`, an attacker can:
- Force Desktop's "Sign in to GitHub Enterprise" OAuth dialog to appear for their own arbitrary domain, which the user believes is a legitimate corporate GitHub Enterprise instance recognized by Desktop.
- Cause an account/endpoint binding (`setSignInEndpoint(origin)` → `beginEnterpriseSignIn`) tied to the attacker's host inside Desktop's `AccountsStore`, which subsequently causes Desktop to automatically fill credentials/tokens for that endpoint on future git operations (`findGitHubTrampolineAccount` matches by origin).
- Skip the one legitimate defense (`isGitHubHost`, a real network probe against `/meta`-style GitHub API detection) that exists specifically to prevent arbitrary hosts from being treated as GitHub/GHE.

This can lead to unauthorized account binding and sets up follow-on credential exfiltration risk (the account's stored token would be attached to fetches/pushes against the attacker's host going forward), which matches the "unauthorized OAuth or account binding" category in the valid-impact list.

### Likelihood Explanation
The trigger requires nothing beyond normal usage: cloning or fetching from any HTTPS remote under attacker control (or a MITM-capable proxy) and having that server return a specific, trivially-crafted `WWW-Authenticate` header on the standard Git credential-challenge round trip. No local access, no prior malware, and no unnatural user steps are required — this is precisely the "attacker controls a git remote/proxy response" primitive called out as valid impact. The main mitigating factor is that the resulting OAuth flow does go to the real host (so no credentials are literally handed to the attacker without user interaction with a real OAuth login page at that origin), and a user paying close attention would see the unfamiliar domain in the sign-in dialog. This nuance is why the severity is best characterized as medium rather than critical, similar to the referenced Vault finding.

### Recommendation
Do not let the `wwwauth[]` realm string alone decide `enterprise` classification. Require confirmation via `isGitHubHost(endpoint)` (or an equivalent authenticated network probe) before treating an unknown host as GitHub/GHE and prompting a GitHub-branded sign-in flow, exactly the way the existing fallback branch does for hosts with no `wwwauth` hint. If the header-based heuristic is kept for UX responsiveness, its result should only be used to *suggest*, not to *bypass* verification — the network check should still run and gate whether the OAuth prompt is shown as "GitHub Enterprise" versus falling back to `promptForCredential` (generic auth) when verification fails.

### Proof of Concept
1. Stand up a plain HTTPS server (no relation to GitHub) that, on any request from Git (e.g. `GET /info/refs?service=git-upload-pack`), responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone or add `https://attacker.example.com/foo.git` as a remote and perform a fetch/pull.
3. Git invokes the trampoline credential helper with `get`, including `wwwauth[]=Basic realm="GitHub"` in stdin (per Git's credential protocol, confirmed by the array-handling test in `credential-test.ts`). [6](#0-5) 

4. `getEndpointKind` classifies the endpoint as `'enterprise'` purely from that header (`app/src/lib/trampoline/trampoline-credential-helper.ts:158-160`) without calling `isGitHubHost`.
5. Since no account exists for `attacker.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://attacker.example.com')`, which pops the "Sign in to GitHub Enterprise" dialog bound to that attacker-controlled origin.

Note: I was not able to inspect the implementation of `isGitHubHost` in `app/src/lib/api.ts` (index did not return its body), so I cannot confirm the exact network check it performs or whether any additional server-side validation exists beyond what's described; this should be verified in a live session before treating the fix as final.

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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-99)
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
```

**File:** app/test/unit/git/credential-test.ts (L8-18)
```typescript
describe('git/credential', () => {
  describe('parseCredential', () => {
    it('expands arrays into numeric entries', async () => {
      assert.deepStrictEqual(
        [...parseCredential('wwwauth[]=foo\nwwwauth[]=bar').entries()],
        [
          ['wwwauth[0]', 'foo'],
          ['wwwauth[1]', 'bar'],
        ]
      )
    })
```
