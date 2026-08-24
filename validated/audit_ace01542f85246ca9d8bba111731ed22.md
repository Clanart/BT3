### Title
GitHub token credential-helper trust is bound to attacker-suppliable host, not to the trampoline token's true remote identity - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The bug-class from the Ambire report is: a nonce/token authenticates "a request is fresh" but never binds *who the counterparty is*, so the same credential/signature can be replayed against a different identity that the check never actually pins down. GitHub Desktop's Git-credential trampoline has the analogous invariant break: the `trampolineToken` (the nonce) only proves "this connection came from a git subprocess Desktop spawned," but the decision to hand back the user's real GitHub OAuth token is keyed purely off the `protocol`/`host` fields Git itself reports for the credential request, not off the actual, trusted remote of the repository/operation that the token was minted for.

### Finding Description
`withTrampolineEnv` mints a short-lived `trampolineToken` via `withTrampolineToken` (`app/src/lib/trampoline/trampoline-tokens.ts:24-37`) and records the operation's context (`trampolineEnvironmentPath.set(token, path)`) in `app/src/lib/trampoline/trampoline-environment.ts:93-146`. `TrampolineServer.processCommand` (`app/src/lib/trampoline/trampoline-server.ts:162-183`) only checks `isValidTrampolineToken(command.trampolineToken)` — i.e. "is this token currently outstanding" — before dispatching the command to the registered handler.

When the command is `get` (a credential lookup, `createCredentialHelperTrampolineHandler`, `app/src/lib/trampoline/trampoline-credential-helper.ts:220-259`), the handler calls `getCredential(input, store, token)`. Inside `getGitHubCredential` (`trampoline-credential-helper.ts:50-57`) the `endpoint` that is looked up against the account store comes from `getCredentialUrl(cred)`, where `cred` is the `protocol=`, `host=`, `path=` key/value payload that the **Git subprocess itself sends over the socket** — this is standard Git credential-helper protocol data, and Git populates it from whatever URL it is currently trying to authenticate against, which is not necessarily the repository's user-configured `origin` remote: [1](#0-0) 

The token/environment-path binding recorded at `withTrampolineEnv` time (`app/src/lib/trampoline/trampoline-environment.ts:93-104`) is never cross-checked against the `host`/`protocol` supplied in the credential request: [2](#0-1) 

So the trampoline token plays exactly the role of the QuickAccount "nonce": it proves freshness/legitimacy of the *channel*, but the actual authorization decision ("should I disclose the GitHub OAuth token for this host") is made from a value the counterparty (Git, driven by repository content/config) controls, not from an identity bound to the token. Git can be made to request credentials for `github.com`/`api.github.com` in the middle of an operation on an untrusted repository through several git-native, attacker-controlled mechanisms that don't require any local/physical access:
- a malicious `.gitmodules` submodule URL pointing at `https://github.com/...`
- an `insteadOf`/`pushInsteadOf` rewrite pulled in via a repo-local config that Desktop applies while operating on a cloned/fetched repo
- an HTTP redirect returned by a malicious server for an HTTPS remote, causing Git to re-prompt credentials for the redirected (github.com) host mid-operation

Because `getGitHubCredential`/`getCredential` (`trampoline-credential-helper.ts:94-135`) only compares `endpoint === account.endpoint` and never verifies that this credential request is scoped to the remote the user actually intended to interact with for that `trampolineToken`'s operation, the trampoline will silently hand back the real account token to a git subprocess whose "identity" (target host) was substituted mid-flight — mirroring the report's "identity is not part of the hash" flaw, just replacing "identity address" with "target host of the credential request."

### Impact Explanation
If exploited, the GitHub OAuth access token stored for the signed-in account is disclosed to a Git operation whose actual network target was influenced by an attacker-controlled repository (submodule URL, config rewrite, or redirect), which can result in credential/token exfiltration to an attacker-controlled endpoint if that same request is subsequently routed there, or unintended authenticated actions being taken with the user's token against `github.com` on behalf of a hostile repository payload — without any credential leak, admin rights, or social engineering being needed beyond the user cloning/fetching/operating on the malicious repo.

### Likelihood Explanation
Medium: this requires the attacker to control a repository the user clones or operates on (a `.gitmodules` URL, a repo-provided config that gets applied, or a malicious/compromised HTTPS remote issuing a redirect) — squarely within the "attacker controls a cloned/fetched repository or a git remote/proxy response" scope defined as valid impact. It does not require local access, leaked credentials, or unusual user steps beyond normal repository operations (clone/fetch/pull) that Desktop performs routinely.

### Recommendation
Bind the credential decision to the operation's actual, expected identity instead of the ad-hoc host string Git reports:
1. When establishing `withTrampolineEnv`, record the repository's trusted remote (origin) host(s) alongside the `trampolineEnvironmentPath` for the token.
2. In `getCredential`/`getGitHubCredential`, before returning first-party (GitHub.com/GHE) credentials, verify that the requested `host`/`protocol` matches one of the remotes explicitly configured for the repository tied to this `trampolineToken`, rejecting (or re-prompting) credential requests for hosts that were not part of the operation's known remote set.
3. Treat `insteadOf`-style rewrites and submodule-triggered sub-fetches as needing re-validation against the top-level operation's trusted host allowlist rather than being trusted implicitly because the token is "currently valid."

### Proof of Concept
Conceptual (verification requires running Desktop against a crafted repo, which was not runnable from static code review):
1. Sign in to GitHub.com in Desktop so `AccountsStore` holds a valid GitHub OAuth token.
2. Clone/add a malicious repository containing a `.gitmodules` entry (or repo-scoped config picked up by the git invocation) that causes Git, mid-operation, to issue a credential request for `host=github.com`/`api.github.com` while Desktop believes it is only operating against the attacker's own remote.
3. Observe that `TrampolineServer` dispatches the `get` credential command using the still-valid `trampolineToken` for that operation; `getGitHubCredential` matches `endpoint` (`github.com`) against the signed-in account and returns the real OAuth token (`trampoline-credential-helper.ts:50-57`) with no check that this host was part of the trusted remotes for the in-flight operation.

Note: I was not able to execute this end-to-end in this environment (no filesystem/terminal access here), so step 2's exact trigger (submodule vs. `insteadOf` vs. redirect) should be validated by a Devin session with repo access to confirm which vector actually reaches the credential-helper trampoline in practice.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L50-57)
```typescript
async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L93-104)
```typescript
export async function withTrampolineEnv<T>(
  fn: (env: object) => Promise<T>,
  path: string,
  isBackgroundTask = false,
  customEnv?: Record<string, string | undefined>
): Promise<T> {
  const sshEnv = await getSSHEnvironment()

  return withTrampolineToken(async token => {
    isBackgroundTaskEnvironment.set(token, isBackgroundTask)
    trampolineEnvironmentPath.set(token, path)

```
