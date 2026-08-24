Based on the local evidence gathered, GitHub Desktop's IPC trust model closely mirrors the reported bug-class: a privileged operation gate ("admin check" in the report) exists in the codebase, but it is inconsistently applied — some IPC channels enforce it, others bypass it entirely.

### Title
Unguarded privileged IPC channels bypass the `isTrustedIPCSender` sender-authorization gate - ([File: app/src/main-process/main.ts])

### Summary
GitHub Desktop enforces IPC sender trust through a central gate: `isTrustedIPCSender` checks a `WebContents.id` against an explicit allow-list (`trustedSenders`) populated only via `addTrustedIPCSender`, and this check is wired into every channel registered through the app's typed wrapper module `app/src/main-process/ipc-main.ts`. [1](#0-0) [2](#0-1) 

However, a large set of highly privileged operations in `app/src/main-process/main.ts` — `open-external`, `move-to-trash`, `show-item-in-folder`, `unsafe-open-directory`, `get-path`, `resolve-proxy`, `install-windows-cli`/`uninstall-windows-cli`, `quit-and-install-updates`, `execute-menu-item-by-id`, and others — are registered with `ipcMain.on(...)`/`ipcMain.handle(...)` calls that appear at file scope in `main.ts` outside of the wrapped `ipc-main.ts` module's `safeListener` path. [3](#0-2) [4](#0-3) 

This is structurally identical to the ERC1967Factory issue: a gate (`isTrustedIPCSender` / "admin check") is designed to protect sensitive state-changing operations, but specific privileged entry points silently omit it, so any caller that can reach those channels is treated as if it were the trusted, authenticated renderer.

### Finding Description
The comment in `trusted-ipc-sender.ts` explicitly states the purpose: "verify that only IPC messages sent from trusted senders are handled, as recommended by the Electron security documentation." [5](#0-4) 
Trust is granted per-`WebContents` instance, explicitly, in `AppWindow`'s and `CrashWindow`'s constructors via `addTrustedIPCSender(this.window.webContents)`. [6](#0-5) 

The enforcement point, however, only exists inside the `on`/`once`/`handle` wrapper functions exported from `app/src/main-process/ipc-main.ts`, which apply `safeListener` before invoking the real handler. [7](#0-6) 
The channel registrations shown in `main.ts` (lines 361-705) instead call `ipcMain.on(...)`/`ipcMain.handle(...)` directly at the top-level of `createMenu`/app setup code, with handlers that perform direct, unguarded native actions: launching arbitrary paths/URLs via `shell.openExternal` (`open-external`), deleting arbitrary files via `shell.trashItem` (`move-to-trash`), revealing/opening arbitrary directories (`show-item-in-folder`, `unsafe-open-directory`), returning internal app paths (`get-path`), and resolving the configured network proxy for an attacker-supplied URL (`resolve-proxy`), none of which re-validate `event.sender` against the trusted set. [3](#0-2) [8](#0-7) 

The corrupted invariant is: "only the main renderer WebContents added via `addTrustedIPCSender` may invoke privileged main-process actions." Because these specific handlers are wired through Electron's raw `ipcMain` rather than the app's `ipc-main.ts` wrapper, that invariant is not enforced for them — the same gap pattern as the admin check being silently dropped from `upgrade`/`upgradeAndCall` while still being documented/expected as the security boundary.

### Impact Explanation
If any additional or lower-privilege `WebContents` exists in the process (e.g., a window/frame not explicitly added to `trustedSenders`, or content loaded into a window before/without a call to `addTrustedIPCSender`), it can directly invoke `shell.openExternal`, `shell.trashItem`, `shell.showItemInFolder`, or `UNSAFE_openDirectory` with attacker-chosen paths/URLs — resulting in arbitrary file deletion, unwanted process/URL launches, or disclosure of local filesystem structure, entirely bypassing the sender-trust boundary the codebase itself documents as the mitigation for untrusted IPC (per the cited Electron security-doc reference).

### Likelihood Explanation
Because `contextIsolation` is disabled and `nodeIntegration` is enabled on the main window's `webPreferences`, the trust boundary between "the main renderer" and "any other content running in an Electron webContents in this app" is exactly what `isTrustedIPCSender` is meant to enforce. [9](#0-8) 
Any handler that skips this gate (as the ones enumerated above appear to) removes that boundary for itself specifically, so the likelihood of exploitation tracks directly with whatever avenue exists for an attacker-influenced frame/window to emit IPC on those channel names — the same "guard removed for specific functions while others remain protected" pattern the external report flagged as critical.

### Recommendation
Route every `ipcMain.on`/`ipcMain.handle` registration for privileged, state-changing, or filesystem/shell-affecting channels (`open-external`, `move-to-trash`, `show-item-in-folder`, `unsafe-open-directory`, `get-path`, `resolve-proxy`, `install-windows-cli`/`uninstall-windows-cli`, `execute-menu-item-by-id`, `quit-and-install-updates`, etc.) through the same `ipc-main.ts` wrapper (`on`/`once`/`handle` exports) that applies `isTrustedIPCSender`, rather than calling Electron's raw `ipcMain` directly in `main.ts`. Audit all direct `ipcMain.on/handle` call-sites in `main.ts` for this omission.

### Proof of Concept
Local code evidence: compare `app/src/main-process/ipc-main.ts:22-66` (gated registration path) against `app/src/main-process/main.ts:581-669` (direct `ipcMain.handle('open-external', ...)`, `ipcMain.handle('move-to-trash', ...)`, `ipcMain.handle('resolve-proxy', ...)`, `ipcMain.on('unsafe-open-directory', ...)`), none of which pass through `safeListener`/`isTrustedIPCSender`. [10](#0-9) [11](#0-10) 

**Uncertainty / what I could not verify:** I was unable to view the top-of-file `import` statements in `main.ts` before line ~118 (outside the returned search windows), so I could not confirm with 100% certainty whether the `ipcMain` identifier used at lines 361-705 resolves to Electron's raw `ipcMain` or to a locally-aliased import of the wrapped `./ipc-main` module. If it is in fact the wrapped module under a different name, this finding does not hold. Given the index's size limits, I recommend a Devin session be used to open the full `app/src/main-process/main.ts` file and confirm the exact import bindings before treating this as confirmed.

### Citations

**File:** app/src/main-process/trusted-ipc-sender.ts (L3-6)
```typescript
// WebContents id of trusted senders of IPC messages. This is used to verify
// that only IPC messages sent from trusted senders are handled, as recommended
// by the Electron security documentation:
// https://github.com/electron/electron/blob/main/docs/tutorial/security.md#17-validate-the-sender-of-all-ipc-messages
```

**File:** app/src/main-process/trusted-ipc-sender.ts (L7-16)
```typescript
const trustedSenders = new Set<number>()

/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)
```

**File:** app/src/main-process/ipc-main.ts (L22-51)
```typescript
export function on<T extends keyof RequestChannels>(
  channel: T,
  listener: RequestChannelListener<T>
) {
  ipcMain.on(channel, safeListener(listener))
}

/**
 * Subscribes to the specified IPC channel and provides strong typing of
 * the channel name, and request parameters. This is the equivalent of
 * using ipcMain.once
 */
export function once<T extends keyof RequestChannels>(
  channel: T,
  listener: RequestChannelListener<T>
) {
  ipcMain.once(channel, safeListener(listener))
}

/**
 * Subscribes to the specified invokeable IPC channel and provides strong typing
 * of the channel name, and request parameters. This is the equivalent of using
 * ipcMain.handle.
 */
export function handle<T extends keyof RequestResponseChannels>(
  channel: T,
  listener: RequestResponseChannelListener<T>
) {
  ipcMain.handle(channel, safeListener(listener))
}
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

**File:** app/src/main-process/main.ts (L439-452)
```typescript
  ipcMain.on('execute-menu-item-by-id', (event, id) => {
    const currentMenu = Menu.getApplicationMenu()

    if (currentMenu === null) {
      return
    }

    const menuItem = currentMenu.getMenuItemById(id)
    if (menuItem) {
      const window = BrowserWindow.fromWebContents(event.sender) || undefined
      const fakeEvent = { preventDefault: () => {}, sender: event.sender }
      menuItem.click(fakeEvent, window, event.sender)
    }
  })
```

**File:** app/src/main-process/main.ts (L581-642)
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

  /**
   * An event sent by the renderer asking for the app's architecture
   */
  ipcMain.handle('get-path', async (_, path) => app.getPath(path))

  /**
   * An event sent by the renderer asking for the app's architecture
   */
  ipcMain.handle('get-app-architecture', async () => getArchitecture(app))

  /**
   * An event sent by the renderer asking for the app's path
   */
  ipcMain.handle('get-app-path', async () => app.getAppPath())

  /**
   * An event sent by the renderer asking for the executable path
   */
  ipcMain.handle('get-exec-path', async () => process.execPath)

  /**
   * An event sent by the renderer asking for whether the app is running under
   * rosetta translation
   */
  ipcMain.handle('is-running-under-arm64-translation', async () =>
    isAppRunningUnderARM64Translation(app)
  )

  /**
   * An event sent by the renderer asking to move the app to the application
   * folder
   */
  ipcMain.handle('move-to-applications-folder', async () => {
    app.moveToApplicationsFolder?.()
  })

  ipcMain.handle('move-to-trash', (_, path) => shell.trashItem(path))
  ipcMain.handle('show-item-in-folder', async (_, path) =>
    shell.showItemInFolder(path)
  )

  ipcMain.on('unsafe-open-directory', async (_, path) =>
    UNSAFE_openDirectory(path)
  )
```

**File:** app/src/main-process/main.ts (L667-669)
```typescript
  ipcMain.handle('resolve-proxy', async (_, url: string) => {
    return session.defaultSession.resolveProxy(url)
  })
```

**File:** app/src/main-process/app-window.ts (L65-72)
```typescript
      webPreferences: {
        // Disable auxclick event
        // See https://developers.google.com/web/updates/2016/10/auxclick
        disableBlinkFeatures: 'Auxclick',
        nodeIntegration: true,
        spellcheck: true,
        contextIsolation: false,
      },
```

**File:** app/src/main-process/app-window.ts (L84-86)
```typescript
    this.window = new BrowserWindow(windowOptions)
    addTrustedIPCSender(this.window.webContents)

```
