## Title
Trampoline credential helper trusts an attacker-controlled `WWW-Authenticate` realm header to classify a remote as a "GitHub" endpoint, bypassing the verified host check — (`app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The report's core pattern is: a function that is *supposed* to restrict handling to a specific, verified category of input (Kitten-only rewards) instead trusts unrestricted, attacker-supplied data through a second code path, causing the wrong logic/credentials to be applied to the wrong "token." The analogous flaw in Desktop is in `getEndpointKind()` inside the git credential-helper trampoline: it classifies a remote host as `'enterprise'` (i.e., a trusted GitHub Enterprise server) purely because the HTTP `WWW-Authenticate` header returned by that remote contains `realm="GitHub"` — a value fully controlled by whoever operates the remote — and it does this *before* falling back to the actual verified check (`isGitHubHost()`, which probes for the `x-github-request-id` response header).

### Finding Description
`getCredential()` first tries an exact-origin match against already-stored accounts [1](#0-0) . If there is no exact match, it calls `getEndpointKind()` to decide how to treat the remote [2](#0-1) .

`getEndpointKind()` contains a "happy path" heuristic that trusts the `wwwauth[...]` credential fields — which Git populates directly from the remote server's `WWW-Authenticate` response header — to decide whether the host is a GitHub Enterprise instance, *before* the safer network-verified `isGitHubHost()` probe is ever reached: [3](#0-2) 

Because any HTTP(S) git server the attacker controls can respond to Git's authentication probe with `WWW-Authenticate: Basic realm="GitHub"`, the attacker fully controls the classification the same way the `incentivize()` caller in the original report fully controlled which "token" was fed into logic meant only for a trusted one (Kitten). Once `endpointKind !== 'generic'` is decided this way, `getCredential()` skips the normal generic-git-auth prompt path (`getGenericCredential` / external credential helper) and instead routes the user into the GitHub sign-in UI: [4](#0-3) 

That UI, `promptForGitHubSignIn()`, begins a GitHub.com or GitHub Enterprise sign-in flow *pointed at the attacker's origin*, because the dialog is populated with `endpoint` (the attacker-controlled URL) and no further validation is performed: [5](#0-4) 

The intended safety net — `isGitHubHost()`, which makes a real network request and checks for the `x-github-request-id` header that only genuine GitHub(.com)/GHE servers return — is only reached as the *last* fallback, after the spoofable header check: [6](#0-5) 

So the existing "guard" (the verified probe) does not protect this path at all, since the unverified `wwwauth[...]` check short-circuits before it.

### Impact Explanation
A user cloning/fetching from, or having Git operate against, an attacker-controlled remote (e.g. a malicious/compromised self-hosted git server, or a MITM git proxy) can force Desktop's credential trampoline to misclassify that host as a trusted "GitHub Enterprise" endpoint. This silently swaps the expected "generic git authentication" dialog for the GitHub-branded sign-in flow, which:
- Diverts the user's authentication flow (and any credentials/PAT they enter, thinking they are dealing with a trusted GitHub/Enterprise prompt) toward the attacker's origin, and
- Persists the resulting `Account` (with a real access token, once obtained) as being associated with the attacker-controlled endpoint, since future lookups match by exact endpoint/origin via `findGitHubTrampolineAccount`.

This is a credential/token-handling trust-boundary break driven entirely by attacker-controlled response data, consistent with the "credential/token exfiltration" and "unauthorized OAuth or account binding" categories in the valid-impact list.

### Likelihood Explanation
Any git host the user interacts with (adding a remote, cloning, fetching from a link/deep link that triggers a clone) can trigger this path the first time Git needs to authenticate against it and there's no already-stored matching account — no local access, admin rights, or social engineering beyond normal use of Desktop against an attacker-supplied remote URL is required. The attacker only needs to control the HTTP response headers of the git server being talked to, which is trivial when the attacker operates that server (or proxies it).

### Recommendation
Do not trust the `wwwauth[...]` `realm=` value as a basis for classifying a host as GitHub/Enterprise. Either:
- Remove the `wwwauth[...]` short-circuit entirely and always fall back to the network-verified `isGitHubHost()` check, or
- If kept as a fast-path optimization, treat a `realm="GitHub"` match only as a *hint* that still requires confirmation via `isGitHubHost()` before routing the user into the trusted GitHub sign-in flow, and never before consulting existing exact-endpoint account matches.

### Proof of Concept
1. Stand up an HTTP git server (e.g. a minimal `git http-backend` wrapper or an nginx auth proxy) at `https://git.attacker.example/`.
2. Configure it to respond to unauthenticated git-over-HTTP requests with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
3. In GitHub Desktop, add this URL as a remote to any repository (or open/clone it, e.g. via a deep link `x-github-client://openRepo/https://git.attacker.example/...` handled by `parseAppURL`/`openOrCloneRepository`), and perform a fetch/push.
4. Git invokes the credential-helper trampoline; `getEndpointKind()` sees the spoofed `wwwauth[...]` field and returns `'enterprise'` at [7](#0-6)  without ever calling `isGitHubHost()`.
5. Because there is no existing account for `git.attacker.example`, Desktop shows `ui.promptForGitHubSignIn('https://git.attacker.example')` instead of the plain generic-auth dialog, misleading the user into a GitHub-style sign-in against the attacker's origin. [8](#0-7)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L1-30)
```typescript
import { AccountsStore } from '../stores'
import { TrampolineCommandHandler } from './trampoline-command'
import { forceUnwrap } from '../fatal-error'
import {
  approveCredential,
  fillCredential,
  formatCredential,
  parseCredential,
  rejectCredential,
} from '../git/credential'
import {
  getCredentialUrl,
  getIsBackgroundTaskEnvironment,
  getTrampolineEnvironmentPath,
  setHasRejectedCredentialsForEndpoint,
} from './trampoline-environment'
import { useExternalCredentialHelper } from './use-external-credential-helper'
import {
  findGenericTrampolineAccount,
  findGitHubTrampolineAccount,
} from './find-account'
import { IGitAccount } from '../../models/git-account'
import {
  deleteGenericCredential,
  setGenericCredential,
} from '../generic-git-auth'
import { urlWithoutCredentials } from './url-without-credentials'
import { trampolineUIHelper as ui } from './trampoline-ui-helper'
import { getAPIEndpoint, isGitHubHost } from '../api'
import { isDotCom, isGHE, isGist } from '../endpoint-capabilities'
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-99)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-105)
```typescript
  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-134)
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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
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

**File:** app/src/lib/api.ts (L2429-2491)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })

    tryUpdateEndpointVersionFromResponse(endpoint, response)

    return response.headers.has('x-github-request-id')
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
```
