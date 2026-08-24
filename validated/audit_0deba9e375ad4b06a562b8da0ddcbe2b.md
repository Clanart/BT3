### Title
Unvalidated `target_url` scheme from legacy commit-status API flows to `shell.openExternal` - (File: app/src/main-process/main.ts)

### Summary
A legacy commit-status API object's `target_url` field is copied verbatim into the `IRefCheck.htmlUrl` used by the CI checks UI, and ultimately reaches `shell.openExternal()` in the main process without any scheme validation.

### Finding Description
`apiStatusToRefCheck` copies the API-controlled `target_url` field directly into `htmlUrl` with no scheme sanitization or validation: [1](#0-0) 

That `htmlUrl` flows to the check-run list UI (`CICheckRunListItem`) and its `onViewCheckExternally` handler, then through `CICheckRunPopover`'s "View check details" action into `dispatcher.openInBrowser(url)`, which is proxied via IPC to the main process's `open-external` handler: [2](#0-1) 

In the main process, the handler only performs an *informational log* check for `http://`/`https://` prefixes — it does not gate or reject any other scheme — before calling `shell.openExternal(path)` unconditionally: [3](#0-2) 

Since `target_url` originates from an untrusted/attacker-influenced API response (a legacy commit status object, e.g. from a third-party CI integration reporting a commit status against a PR/branch the victim views), an attacker can set `target_url` to `file:///etc/passwd`, a custom protocol handler (e.g. `mailto:`, `ms-word:`, or a registered app URI scheme), or other non-http(s) URI, and it will be passed unmodified to Electron's `shell.openExternal`.

### Impact Explanation
`shell.openExternal` with a `file://` URI can open local files or directory listings in the user's file manager/browser depending on OS, and with a registered custom protocol handler can invoke arbitrary installed applications with attacker-controlled arguments, which historically has been used as a code-execution primitive in other Electron apps (e.g. abusing protocol handlers such as `search-ms:`, `outlook:`, `mailto:`, or malformed URIs to trigger command injection into a handler application). This breaks the intended http(s)-only invariant for "open in browser" actions and expands the trust boundary from remote web content to arbitrary local URI-scheme handling, which is a real (if handler-dependent) risk surface.

### Likelihood Explanation
The `target_url` field on a commit status is populated by whatever CI integration/App posted the status to the commit — this is often attacker-influenced content (e.g., any GitHub App/Integration with `statuses:write` on a repo, or a malicious CI system configured on a fork/PR) and is not sanitized by GitHub's API for scheme. A user only needs to view the CI checks popover for the affected commit and click "View check details" — a normal, expected user action, not a contrived one. This makes the path reasonably reachable without special access, though it does require the user to click through to view details on a specific check.

### Recommendation
Enforce the same origin/scheme allowlist for all external open flows: validate that `htmlUrl`/`target_url` begins with `http://` or `https://` before allowing the value into `dispatcher.openInBrowser`, or better, harden the main-process `open-external` IPC handler in `app/src/main-process/main.ts` to reject (not just log) any `path` whose scheme isn't `http:`/`https:` before calling `shell.openExternal`. Consider using the `url` module to parse and check `protocol` rather than string prefix matching, and reject/no-op for anything else.

### Proof of Concept
1. Mock a legacy commit-status API response for a PR/branch's combined status such that one status item has:
```json
{ "id": 1, "context": "ci/legacy", "state": "success", "target_url": "file:///etc/passwd" }
```
2. This flows through `apiStatusToRefCheck` → `htmlUrl: "file:///etc/passwd"` (`app/src/lib/ci-checks/ci-checks.ts:78`).
3. Render the CI checks popover for that ref; the item's `htmlUrl` is non-null so the "View check details" / external-link action is rendered/enabled in `CICheckRunListItem`.
4. Click "View check details" → `CICheckRunPopover.onViewCheckDetails` → `dispatcher.openInBrowser('file:///etc/passwd')` → IPC `open-external` invoke → `app/src/main-process/main.ts:581-597` calls `shell.openExternal('file:///etc/passwd')` unconditionally (the `http(s)` check on line 583-586 only gates a log statement, not the call itself).
5. Assert: `shell.openExternal` is called with the raw non-http(s) value `file:///etc/passwd` (or a custom protocol handler URI), confirming the missing scheme validation.

### Citations

**File:** app/src/lib/ci-checks/ci-checks.ts (L70-79)
```typescript
  return {
    id: apiStatus.id,
    name: apiStatus.context,
    description: getCheckRunShortDescription(state, conclusion),
    status: state,
    conclusion,
    appName: '',
    checkSuiteId: null,
    htmlUrl: apiStatus.target_url,
  }
```

**File:** app/src/ui/main-process-proxy.ts (L146-146)
```typescript
export const openExternal = invokeProxy('open-external', 1)
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
