### Title
Attacker-controlled `WWW-Authenticate` realm spoofing forces silent GitHub Enterprise account-binding sign-in prompt for arbitrary hosts - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Reducing the "sell destroy" bug class to its invariant: an attacker-controlled input (transfer-to-pair) is fed unchecked into a state mutation (`burn`) and then forced through a re-sync (`sync()`) that the rest of the system trusts as ground truth, skewing a security-relevant computation (price). The GitHub Desktop analog is `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, which classifies an arbitrary remote host as a trusted "GitHub Enterprise" endpoint based solely on an attacker-supplied `WWW-Authenticate` header value, with no host/certificate binding. That corrupted classification is then "synced" into the UI trust layer by triggering `trampolineUIHelper.promptForGitHubSignIn`, which begins an Enterprise OAuth/account-binding flow scoped to the attacker's host.

### Finding Description
When Git performs an HTTP(S) operation (clone, fetch, push, submodule update, LFS transfer) against a remote that returns `401` with a `WWW-Authenticate` header, Git forwards that header verbatim to the configured credential helper as `wwwauth[n]=...`. GitHub Desktop's credential-helper trampoline consumes this attacker-controlled string directly: [1](#0-0) 

```
// When Git attempts to authenticate with a host it captures any
// WWW-Authenticate headers and forwards them to the credential helper. We
// use them as a happy-path to determine if the host is a GitHub host...
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
```

Any remote server the app talks to during a normal Git operation — including a submodule URL embedded in a cloned/fetched repository's `.gitmodules`/`config` (attacker-controlled per `app/src/lib/git/submodule.ts` and `app/test/fixtures/*/config`) — can simply answer with `WWW-Authenticate: Basic realm="GitHub"` to have Desktop's `getEndpointKind` classify it as `'enterprise'`, with zero validation of the actual hostname, TLS identity, or any handshake with a real GitHub API.

That misclassification then flows, unguarded, into `getCredential`: [2](#0-1) 

Since the attacker's endpoint will not match any stored `Account.endpoint`, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

This directly invokes `dispatcher.beginEnterpriseSignIn` / `setSignInEndpoint(origin)` and shows the standard `PopupType.SignIn` dialog (`isCredentialHelperSignIn: true`) pre-bound to the attacker's `origin` — the exact same trusted "Sign in to GitHub Enterprise" UI a user would see for a legitimate corporate GHE server, but triggered purely by the content of an HTTP response header during an otherwise unattended background git operation (e.g. `submodule update --init --recursive` after a clone, or `git pull` for LFS), not by the user typing an enterprise URL themselves.

The relevant guard, `getIsBackgroundTaskEnvironment(token)`, only suppresses the prompt for operations explicitly marked background; interactive operations (an ordinary user-initiated `Clone`, `Fetch`, `Pull`) are not "background" and will surface the prompt.

### Impact Explanation
The corrupted value is the `endpointKind` classification (`'enterprise'` vs `'generic'`), computed purely from attacker-controlled bytes with no cryptographic/hostname binding. It changes two security decisions:
1. It bypasses the safe path (generic-credential prompt / external helper `fillCredential`) and instead invokes the GitHub-branded Enterprise sign-in flow (`showEnterpriseSignInDialog`/OAuth), matching the report's "unauthorized OAuth or account binding" impact category — a user who completes this flow believing it's a legitimate corporate SSO/OAuth prompt actually authenticates and binds a GitHub Enterprise `Account` to the attacker's `origin`.
2. It also short-circuits `storeCredential`/`eraseCredential` (both early-return when `endpointKind !== 'generic'`), silently suppressing Desktop's own generic credential persistence for that host — corrupting what would otherwise be stored/reused for future authentication decisions to that same origin, analogous to the exploit's forced re-sync of a manipulated reserve becoming the new trusted state.

Full assessment of downstream account-token compromise (i.e., whether the bound Enterprise account's OAuth token can subsequently be replayed against real github.com/GHE endpoints) could not be fully verified from the indexed code alone; `findGitHubTrampolineAccount` matches by URL `origin`, which appears to scope reuse to the attacker's own host, limiting direct token theft. This is a real gap in my analysis and would need to be confirmed with the full `AccountsStore`/`api.ts` implementation, which is not fully covered by the code index.

### Likelihood Explanation
The trigger is low-effort for an attacker who controls any remote the app talks to (a malicious clone/fetch source, a `.gitmodules` submodule URL embedded in an otherwise benign-looking repository, or an LFS endpoint): simply respond to the HTTP request with a `401` and `WWW-Authenticate: Basic realm="GitHub"`. No user interaction is required to reach the prompt — only completing the resulting sign-in dialog requires user action, and that action looks identical to a legitimate flow, satisfying the task's requirement of attacker control over "a cloned/fetched repository ... or a git remote/proxy response" without local access, admin rights, or prior host compromise.

### Recommendation
Do not trust the `WWW-Authenticate` realm string as a substitute for host identity. Restrict `'enterprise'`/`'github.com'` classification (and therefore the branded sign-in flow) to hosts that are already known GitHub endpoints (`isDotCom`/`isGHE`/existing accounts) or hosts independently verified via `isGitHubHost(endpoint)` (an actual API probe), and drop the `wwwauth[]` realm heuristic in `getEndpointKind`, or at minimum require it to agree with a live `isGitHubHost` check before elevating trust and prompting an OAuth/account-binding flow.

### Proof of Concept
1. Attacker publishes/serves a repository containing a `.gitmodules` (or plain remote) URL such as `https://evil.example.com/x`.
2. Victim clones the repository in GitHub Desktop, triggering `updateSubmodulesAfterOperation`/fetch (`app/src/lib/git/submodule.ts`).
3. `evil.example.com` responds to Git's HTTPS request with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this to Desktop's credential helper trampoline; `getEndpointKind` (`trampoline-credential-helper.ts:153-165`) returns `'enterprise'` purely from the header text.
5. `getCredential` (lines 93-125) finds no matching account for `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
6. `TrampolineUIHelper.promptForGitHubSignIn` (`trampoline-ui-helper.ts:80-104`) opens the standard `SignIn` popup with `beginEnterpriseSignIn`, pre-set to the attacker's origin — indistinguishable in appearance from a legitimate corporate GHE sign-in triggered by the user's own IT-managed server.

### Citations

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
