## Title
Credential-helper endpoint classification trusts an attacker-supplied `WWW-Authenticate` header to trigger a "Sign in to GitHub Enterprise" prompt for an arbitrary git remote, enabling account binding to an attacker-controlled endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

## Summary
`getEndpointKind()` classifies a git credential request as `'enterprise'` based purely on the *content of a header returned by the remote server itself* (`WWW-Authenticate: ... realm="GitHub"`), rather than on any property Desktop actually controls or verifies (e.g., a known/registered endpoint, TLS identity, or a successful `/api/v3` probe). This mirrors the seed bug's broken invariant: a security-relevant value (here, "is this a trusted GitHub identity?") is derived from context that changes at the whim of whoever is on the other end of the call, rather than from an invariant established by the caller/Desktop itself.

## Finding Description
The credential trampoline `getCredential()` flow is invoked whenever `git` (clone/fetch/push/pull, including for submodules or LFS) needs credentials for *any* remote host [1](#0-0) . To decide how to react, `getEndpointKind()` first tries strict checks (`isGist`, `isDotCom`, `isGHE`, existing-account origin match), but if none of those match it falls back to trusting an HTTP response header that the *remote server controls*: [2](#0-1) 

Concretely: when Git tries to authenticate against a host and gets a `401` with `WWW-Authenticate: Basic realm="GitHub"`, git forwards this header to the credential helper (`wwwauth[...]`), and Desktop treats that as proof the host is a GitHub Enterprise instance, setting `endpointKind = 'enterprise'`.

Back in `getCredential()`, because the endpoint is not `'generic'` and there's no existing account bound to that exact API endpoint, Desktop does **not** silently supply any existing token; instead it calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` then drives the same "Sign in to GitHub/Enterprise" UI that's used for legitimate first-party auth, seeding it with the attacker-controlled `endpoint` and `isCredentialHelperSignIn: true`: [4](#0-3) 

The important point is *what triggers this dialog*: nothing the user clicked — it's the direct, automatic consequence of a git network operation against an attacker-controlled remote (e.g. a cloned/fetched repository, or a repository's declared submodule URL) that returns a specific header value. The existing "guards" (`isDotCom`, `isGHE`, existing-account origin matching in `findGitHubTrampolineAccount`, which does a strict `origin` comparison) only stop the automatic release of an *already-stored* token to a mismatched host — they do nothing to stop this fallback branch, because the fallback exists specifically to handle the case where none of those strict checks matched, i.e. exactly the case of a brand-new, previously-unseen host.

## Impact Explanation
This is a fully attacker-controlled entry point: an attacker only needs to control a git server that a victim's Desktop client talks to via a normal operation (a remote the user added/cloned, or a submodule/`.gitmodules` URL embedded in a cloned repository) and have it answer HTTP Basic-auth challenges with `WWW-Authenticate: Basic realm="GitHub"`. Desktop will then present its native "Sign in to GitHub Enterprise" dialog for that attacker's endpoint. If the user completes sign-in (PAT entry or OAuth against the attacker endpoint), the resulting `Account` — with a real credential/token — is stored by Desktop bound to the attacker's endpoint (`isCredentialHelperSignIn` flow → `resolveOAuthRequest`/PAT flow in `sign-in-store.ts`). This is an unauthorized OAuth/account-binding outcome: the attacker's host is now treated by Desktop as a legitimate Enterprise endpoint the user is "signed in" to, and depending on how the credential is subsequently released to Git it can be sent to that host on future operations. No local access, malware, or prior credential leak is required — only that the victim clone/fetch from or add a repository the attacker controls.

## Likelihood Explanation
Medium. The primitive itself (spoofing `WWW-Authenticate: realm="GitHub"` from any HTTP git server) is trivial for an attacker who runs the git server the victim connects to (a common scenario: a malicious or compromised third-party git host, a submodule pointing to attacker infrastructure, or a corporate proxy/MITM situation). What's *not* fully verifiable from the codebase in isolation is exactly how far this reaches — i.e. whether the account subsequently gets treated identically to a legitimate GHE account for auto-supplying credentials on future requests, since `findGitHubTrampolineAccount` still requires an exact `origin` match before silently handing out a token. That part of the chain (does the newly bound "enterprise" account effectively give the attacker anything more than a phishing-style credential-capture UI, versus real automatic token leakage on subsequent requests) needs to be confirmed by a Devin session with the ability to run the trampoline end-to-end and trace what account data is persisted and reused afterward.

## Recommendation
Do not classify a host as a trusted GitHub/Enterprise endpoint based on a self-declared `WWW-Authenticate` realm value. Require an explicit, user-initiated registration of Enterprise endpoints (as already happens via the normal "Sign in to GitHub Enterprise" flow entered from Preferences) before automatically surfacing sign-in prompts driven by network responses, or at minimum perform a genuine identity check (e.g., successful `/api/v3/meta` call plus certificate/host validation) rather than trusting the header content, and clearly indicate in the sign-in UI that the endpoint was inferred from an untrusted server response rather than user input.

## Proof of Concept
1. Attacker stands up a plain HTTP(S) git server (or embeds it as a submodule URL in a repository the victim clones) that, on any Basic-auth request, returns `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim, using GitHub Desktop, clones/fetches/pushes to that remote (directly, or transitively via `git submodule update` triggered by opening/pulling a repository containing the malicious submodule).
3. Git invokes the Desktop credential helper trampoline; `getEndpointKind()` sees the `wwwauth[...]` entry matching `realm="GitHub"` and returns `'enterprise'` [5](#0-4) .
4. `getCredential()` finds no existing account for that endpoint and calls `ui.promptForGitHubSignIn(endpoint)`, showing the victim a native "Sign in to GitHub Enterprise" dialog for the attacker's URL [6](#0-5) [4](#0-3) .
5. If the victim completes sign-in, Desktop stores an `Account` bound to the attacker's endpoint.

I was unable to fully trace the downstream persistence/reuse of that account within the remaining investigation budget; a Devin session with full repo/runtime access should verify (a) exactly what is stored in `AccountsStore` after this flow completes, and (b) whether/how that account is later auto-supplied to the same or other hosts without further prompting.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-178)
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
