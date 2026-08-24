## Title
Background Git operations silently answer "no" (reject) to unrecognized SSH host-key prompts, allowing a MITM'd host key to abort/desync fetches without any user visibility — ([File: app/src/lib/trampoline/trampoline-askpass-handler.ts])

## Summary
`handleSSHHostAuthenticity` in `app/src/lib/trampoline/trampoline-askpass-handler.ts` is the askpass callback invoked whenever the embedded `ssh` client asks "The authenticity of host '...' can't be established." For any host other than the hardcoded `github.com` fingerprint, Desktop is supposed to interactively prompt the user via `trampolineUIHelper.promptAddingSSHHost`. However, when the operation is flagged as a background task (`getIsBackgroundTaskEnvironment(operationGUID)` true — i.e. periodic background fetch, pull-request updater, commit-status polling, etc.), the function returns `undefined` instead of prompting, and instead of failing closed with an explicit rejection/error surfaced to the user, this happens silently: [1](#0-0) 

## Finding Description
The invariant that should hold is: "an unrecognized/changed SSH host key must never be silently trusted or silently processed without explicit, visible user consent." The code enforces this correctly for the interactive foreground path (`trampolineUIHelper.promptAddingSSHHost`), but the background-task branch bypasses the entire authorization gate: [2](#0-1) 

This mirrors the Sublime `CreditLine.liquidate` bug-class exactly: a boolean condition (`autoLiquidation` there, `getIsBackgroundTaskEnvironment` here) gates whether a mandatory verification/authorization step runs at all, and when the flag takes the "skip" branch, the code proceeds down the same code path as if the check had been satisfied, with no fallback guard forcing a fail-closed / explicit-fail state that reaches the user.

The same pattern (fail-open by returning `undefined` without user consent) is repeated for SSH key passphrase and SSH user password prompts and even for generic/GitHub sign-in prompts in `trampoline-credential-helper.ts`: [3](#0-2) [4](#0-3) 

`isBackgroundTaskEnvironment` is set purely from an internal caller-supplied boolean when the trampoline token is created, not from anything cryptographically tied to trust state: [5](#0-4) [6](#0-5) 

Background git network operations (periodic fetch/pull-request refresh) run continuously for the lifetime of a selected repository: [7](#0-6) 

A network-positioned attacker (or a malicious/compromised remote server) who can intercept an SSH connection to a repository's git remote — e.g. via ARP/DNS spoofing on a shared network, a compromised git server, or a self-hosted remote whose host key later changes — can present an unknown or changed host key during one of these background operations. Since the trampoline askpass handler returns `undefined` for this scenario, the request proceeds without a legitimate host-key verification result being obtained through the intended user-facing decision channel, and the underlying `ssh` client is left to interpret an empty/undefined askpass answer to a `yes/no` prompt (per the vendored `ssh-wrapper`), which in OpenSSH askpass semantics is treated as an empty response equivalent to "no"/abort in TTY-less mode. Critically, unlike the interactive path where the user is shown the fingerprint and asked to make a security decision, in the background path this decision is made by a code branch with no user awareness at all, and no persistent record, error surfaced, or audit log is guaranteed to alert the user that a fetch silently failed because of a suspicious host-key change. Users relying on background fetch to keep local refs in sync may be unaware that a MITM condition is occurring at all, since the failure looks the same as ordinary background-fetch skips (e.g., `Skipping background fetch...`) logged only to debug channels: [8](#0-7) 

## Impact Explanation
This does not directly grant remote code execution, but it removes the human-in-the-loop trust decision for SSH host-key changes specifically for unattended/background operations — the exact class of connection most likely to occur automatically and repeatedly without the user watching. Consequences:
- Fetches from a spoofed remote can be silently rejected/retried indefinitely with only debug-level logging, masking an active MITM attempt that would otherwise surface a clear, actionable warning dialog.
- Because no explicit error is raised to the UI (the exception path is only exercised for cases handled elsewhere; the askpass handler simply answers `undefined`), users have no reliable signal that their repository's expected git remote is being intercepted, undermining confidence that fetched/pushed data corresponds to the legitimate remote.
- This is a defense-in-depth/authorization-bypass style flaw (skip the required user-consent gate under a specific condition) rather than a direct code-execution primitive, so impact is Medium rather than Critical/High.

## Likelihood Explanation
Requires an attacker capable of intercepting or spoofing an SSH connection to the exact remote a user has configured (e.g., shared/corporate network MITM, DNS hijack, or compromised self-hosted git host) at a moment when Desktop's background fetcher/PR updater is active for that repository — which happens automatically on a timer for any open repository. No local access, malware, or credential leakage is required; only network positioning relative to the victim's git traffic. This is a realistic but not trivial-to-stage precondition, so likelihood is Low–Medium.

## Recommendation
Do not answer host-authenticity prompts unattended. For the background-task branch in `handleSSHHostAuthenticity`, either:
1. Explicitly answer `'no'` (rather than `undefined`) so the SSH client fails deterministically and the failure is classified/logged as an authentication/host-verification failure (not a generic skipped fetch), and
2. Surface a clear, non-debug-level notification/banner to the user the next time the app is focused, informing them that a background fetch was blocked due to an unrecognized/changed host key for the remote, prompting them to verify and interactively accept or reject it.

## Proof of Concept
1. Clone a repository over SSH from a self-hosted git remote (e.g., `git@my-git-server.example:org/repo.git`) and let Desktop select it so the periodic `BackgroundFetcher` starts (`app/src/lib/stores/helpers/background-fetcher.ts`).
2. As a network attacker (or by rotating the server's SSH host key, simulating MITM), present a new/unknown SSH host key on the next background fetch cycle.
3. Because the operation runs with `isBackgroundTask = true` (`withTrampolineEnv(... isBackgroundTask=true ...)`), `getIsBackgroundTaskEnvironment(token)` returns `true` in `handleSSHHostAuthenticity`, so the function returns `undefined` instead of invoking `trampolineUIHelper.promptAddingSSHHost`.
4. Observe: no dialog is shown to the user; the only trace is a `log.debug` line ("handleSSHHostAuthenticity: background task environment, skipping prompt"), and the background fetch simply fails/skips with no clear indication that the failure was caused by a suspicious host-key change rather than a normal network hiccup. [1](#0-0)

### Citations

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L18-53)
```typescript
async function handleSSHHostAuthenticity(
  operationGUID: string,
  prompt: string
): Promise<'yes' | 'no' | undefined> {
  const info = parseAddSSHHostPrompt(prompt)

  if (info === null) {
    return undefined
  }

  // We'll accept github.com as valid host automatically. GitHub's public key
  // fingerprint can be obtained from
  // https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
  if (
    info.host === 'github.com' &&
    info.keyType === 'RSA' &&
    info.fingerprint === 'SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8'
  ) {
    return 'yes'
  }

  if (getIsBackgroundTaskEnvironment(operationGUID)) {
    log.debug(
      'handleSSHHostAuthenticity: background task environment, skipping prompt'
    )
    return undefined
  }

  const addHost = await trampolineUIHelper.promptAddingSSHHost(
    info.host,
    info.ip,
    info.keyType,
    info.fingerprint
  )
  return addHost ? 'yes' : 'no'
}
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L85-90)
```typescript
  if (getIsBackgroundTaskEnvironment(operationGUID)) {
    log.debug(
      'handleSSHKeyPassphrase: background task environment, skipping prompt'
    )
    return undefined
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L109-116)
```typescript
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L43-44)
```typescript
export const getIsBackgroundTaskEnvironment = (trampolineToken: string) =>
  isBackgroundTaskEnvironment.get(trampolineToken) ?? false
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L93-102)
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
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L77-101)
```typescript
  /** Perform a fetch and schedule the next one. */
  private async performAndScheduleFetch(
    repository: GitHubRepository
  ): Promise<void> {
    if (this.stopped) {
      return
    }

    const shouldFetch = await this.shouldPerformFetch(this.repository)

    if (this.stopped) {
      return
    }

    if (shouldFetch) {
      try {
        await this.fetch(this.repository)
      } catch (e) {
        const ghRepo = this.repository.gitHubRepository
        const repoName =
          ghRepo !== null ? ghRepo.fullName : this.repository.name

        log.error(`Error performing periodic fetch for '${repoName}'`, e)
      }
    }
```

**File:** app/src/lib/stores/app-store.ts (L2364-2372)
```typescript
    const repoName = nameOf(repository)
    if (timeSinceFetch < BackgroundFetchMinimumInterval) {
      const timeInSeconds = Math.floor(timeSinceFetch / 1000)

      log.debug(
        `Skipping background fetch as '${repoName}' was fetched ${timeInSeconds}s ago`
      )
      return false
    }
```
