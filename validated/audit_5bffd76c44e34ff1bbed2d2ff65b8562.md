### Title
Unvalidated GitHub Checks API `html_url` passed to `shell.openExternal` allows arbitrary URI-scheme execution - ([File: app/src/main-process/main.ts])

### Summary
The oracle report's core flaw is that a value obtained from an external/attacker-influenceable source (cached price) is consumed by a security-relevant decision (`getCollateralRatio`) without being revalidated or refreshed first. The Desktop analog is structurally identical: a value obtained from an external, attacker-influenceable source — the `html_url`/`target_url` field of a GitHub Checks-API check run — is consumed by a security-relevant sink (`shell.openExternal`) without being validated (scheme-checked) first, even though Desktop already has a "validate before trusting" pattern in place elsewhere (`validateURL` in `app/src/ui/lib/enterprise-validate-url.ts`) that is simply not applied here.

### Finding Description
`IRefCheck.htmlUrl` is populated straight from the GitHub Checks API response and surfaced to the renderer via `app/src/lib/ci-checks/ci-checks.ts`. Any actor who can produce check-run/status data for a repository the user has open in Desktop — e.g. a third‑party CI integration, a malicious/compromised GitHub App with checks:write permission, or a status-reporting workflow — fully controls the string that ends up in `checkRun.htmlUrl`.

That value is used directly, with no scheme/host allow-listing, in two UI call sites: [1](#0-0) [2](#0-1) 

Both call `dispatcher.openInBrowser(url)`, which flows to `AppStore._openInBrowser`: [3](#0-2) 

In the renderer/main IPC bridge, the equivalent `open-external` handler in the main process also performs no scheme restriction — it only *logs* differently for `http/https` but calls `shell.openExternal(path)` unconditionally for any string: [4](#0-3) 

Compare this with the one place Desktop *does* correctly treat an externally-supplied URL as untrusted input before acting on it — `validateURL` for GitHub Enterprise server addresses, which explicitly rejects any protocol other than `https:`: [5](#0-4) 

No equivalent check exists for `checkRun.htmlUrl` (or the fallback `repository.htmlURL`/PR URL construction) before it reaches `shell.openExternal`. This is the exact "cached/unrefreshed value trusted at the point of a sensitive action" pattern from the source report, translated from "stale oracle price used in a collateral-ratio calculation" to "attacker-supplied API string used in an OS shell-open call."

### Impact Explanation
`shell.openExternal` in Electron hands the string to the OS shell/registry handler for whatever URI scheme is present. If the attacker-controlled `html_url` is not restricted to `http(s)`, it can carry schemes that are handled by other installed applications or the OS shell itself (e.g. `file://` opening a local file, custom registered protocol handlers, or platform-specific handler chains). Depending on the OS and what handlers happen to be registered on the victim's machine, this can result in unexpected local file access/execution or triggering of arbitrary installed-application behavior purely by the victim viewing check results in Desktop and clicking "View on GitHub" / "View check details" — actions a normal user takes routinely and does not perceive as risky. This falls squarely in the requested impact class: an attacker-controlled GitHub API object driving code execution / file access outside the expected scope, without any unnatural user interaction.

### Likelihood Explanation
Likelihood is moderate: it requires the victim's repository to have a status/check provider (CI integration or GitHub App) that an attacker can influence, and requires the user to click through to view checks — a completely ordinary, expected workflow in Desktop (checks dialogs and notifications are a core, frequently-used feature for anyone with CI configured). No admin rights, local access, or prior malware are needed; the primitive is simply "attacker can create/modify a check run/status pointing at a crafted URL," which is realistic for supply-chain/CI-integration threat models (compromised Action, malicious third-party app, forked-repo CI abuse).

### Recommendation
Apply the same discipline used in `enterprise-validate-url.ts` to any URL sourced from GitHub API objects before it is passed to `shell.openExternal`:
- In `AppStore._openInBrowser` (`app/src/lib/stores/app-store.ts`) and/or the main-process `open-external` IPC handler (`app/src/main-process/main.ts`), parse the URL and reject/strip anything whose scheme is not `http:`/`https:`.
- Specifically validate `checkRun.htmlUrl` (and any other API-sourced `html_url`/`target_url` fields) at the point they are received from the API (`app/src/lib/ci-checks/ci-checks.ts`), not just at the final call site, so all consumers benefit uniformly.

### Proof of Concept
1. Attacker controls (or compromises) a CI integration / GitHub App that posts check runs to a repository the victim has cloned in Desktop.
2. Attacker sets the check run's `details_url`/`html_url` to a non-http(s) URI (e.g., a `file://` path or a registered custom-scheme handler URI) instead of a normal GitHub URL.
3. This propagates untouched through `ci-checks.ts` into `IRefCheck.htmlUrl`.
4. Victim opens the "Checks failed" notification (`pull-request-checks-failed.tsx`) or the check-run popover (`ci-check-run-popover.tsx`) and clicks "View on GitHub" / a check row.
5. `onViewOnGitHub`/`onViewCheckDetails` calls `dispatcher.openInBrowser(url)` → `AppStore._openInBrowser` → `shell.openExternal(url)` with the attacker-chosen scheme, with no validation performed anywhere in the chain, unlike the `https:`-only enforcement present for enterprise server URLs.

Note: I could not fully confirm from the indexed code alone which combinations of registered OS URI-scheme handlers on a target machine turn this into full code execution versus merely opening an unexpected file/app — that depends on the victim's installed software and OS, which is outside what the repository index can show. A Devin session with a live environment would be needed to enumerate concrete exploitable handler chains per platform.

### Citations

**File:** app/src/ui/check-runs/ci-check-run-popover.tsx (L159-168)
```typescript
    // Some checks do not provide htmlURLS like ones for the legacy status
    // object as they do not have a view in the checks screen. In that case we
    // will just open the PR and they can navigate from there... a little
    // dissatisfying tho more of an edgecase anyways.
    const url =
      checkRun.htmlUrl ??
      `${this.props.repository.htmlURL}/pull/${this.props.prNumber}`

    this.props.dispatcher.openInBrowser(url)
    this.props.dispatcher.incrementMetric('viewsCheckOnline')
```

**File:** app/src/ui/notifications/pull-request-checks-failed.tsx (L375-390)
```typescript
  private onViewOnGitHub = (checkRun: IRefCheck) => {
    const { repository, pullRequest, dispatcher } = this.props

    // Some checks do not provide htmlURLS like ones for the legacy status
    // object as they do not have a view in the checks screen. In that case we
    // will just open the PR and they can navigate from there... a little
    // dissatisfying tho more of an edgecase anyways.
    const url =
      checkRun.htmlUrl ??
      `${repository.gitHubRepository.htmlURL}/pull/${pullRequest.pullRequestNumber}`
    if (url === null) {
      // The repository should have a htmlURL.
      return
    }
    dispatcher.openInBrowser(url)
  }
```

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```

**File:** app/src/main-process/main.ts (L581-597)
```typescript
  ipcMain.handle('open-external', async (_, path: string) => {
    const pathLowerCase = path.toLowerCase()
    if (
      pathLowerCase.startsWith('http://') ||
      pathLowerCase.startsWith('https://')
    ) {
      log.info(`opening in browser: ${path}`)
    }

    try {
      await shell.openExternal(path)
      return true
    } catch (e) {
      log.error(`Call to openExternal failed: '${e}'`)
      return false
    }
  })
```

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
}
```
