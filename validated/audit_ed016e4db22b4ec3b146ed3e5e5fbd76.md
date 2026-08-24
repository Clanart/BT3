## Title
`ipcMain.on`/`ipcMain.handle` calls in `main.ts` bypass the trusted-IPC-sender guard - (File: `app/src/main-process/main.ts`)

## Summary
The report's underlying pattern is "a security guard function exists in the codebase but is not invoked at the enforcement point that needs it." The equivalent invariant break in GitHub Desktop is `isTrustedIPCSender`, a guard defined specifically to stop untrusted `WebContents` from issuing privileged IPC calls, which is enforced only by the wrapped `on`/`handle`/`once` helpers in `app/src/main-process/ipc-main.ts`. `main.ts` registers the vast majority of its IPC channels (51 matches) directly against Electron's raw `ipcMain.on`/`ipcMain.handle`, which never calls `safeListener`/`isTrustedIPCSender`, so those channels are unprotected by the very check the codebase built to prevent this class of issue.

## Finding Description
`app/src/main-process/trusted-ipc-sender.ts` maintains a set of trusted `WebContents` ids and exposes `isTrustedIPCSender`, explicitly citing Electron's guidance to "validate the sender of all IPC messages": [1](#0-0) 

The intended enforcement point is `app/src/main-process/ipc-main.ts`, whose `on`/`once`/`handle` wrappers call `safeListener`, which rejects any event whose sender is not in the trusted set: [2](#0-1) 

`addTrustedIPCSender` is only invoked from `app-window.ts` and `crash-window.ts`, establishing the trust boundary at window creation: [3](#0-2) 

However, `app/src/main-process/main.ts` registers its IPC channels directly with Electron's native `ipcMain.on(...)`/`ipcMain.handle(...)` (51 occurrences) instead of going through the `on`/`handle` wrappers exported by `ipc-main.ts`. Because `safeListener` — and therefore `isTrustedIPCSender` — is never invoked on this call path, any of these 51 channels accept messages from *any* sender frame in the renderer process, including an untrusted subframe (e.g., a compromised or malicious renderer content surface such as a rendered PR body, image proxy iframe, or any other webContents that ends up loaded inside the app), not just the trusted top-level `WebContents` registered via `addTrustedIPCSender`.

This mirrors the H-03 pattern exactly: a purpose-built revocation/validation primitive (`revoke_freeze_authority` there, `isTrustedIPCSender` here) exists in the codebase but is not wired into the code path that creates the actual attack surface (pool creation there, IPC channel registration here), silently leaving the protected capability reachable by an untrusted actor.

## Impact Explanation
If an attacker can get arbitrary or attacker-influenced content executing in any renderer frame that is not the main trusted `WebContents` (for example via a compromised subframe, a malicious webview, or content-injection in a rendered view), they could invoke any of the IPC channels registered directly in `main.ts` without passing the `isTrustedIPCSender` check that the rest of the app relies on as its IPC trust boundary. Depending on which of the 51 channels are affected, this could translate into privileged main-process actions being triggered by untrusted renderer content — a renderer-sandbox/IPC-boundary escape, which is explicitly in scope per the task's valid-impact criteria.

## Likelihood Explanation
Likelihood is Medium: exploitation requires the attacker to first get code running in some renderer frame within the Desktop process (e.g., through a malicious repository's rendered content, a crafted link/deep-link opened in an in-app surface, or a GitHub API-served object rendered as HTML/markdown). Given Desktop's architecture already anticipates this exact threat (hence the existence of `trusted-ipc-sender.ts` and the safe-listener wrapper), the missing enforcement in `main.ts` represents a real gap rather than a defense-in-depth nicety — the guard was clearly designed to cover this exact registration surface but doesn't.

## Recommendation
Route all IPC channel registrations in `app/src/main-process/main.ts` through the `on`/`handle`/`once` wrappers exported from `app/src/main-process/ipc-main.ts` instead of calling Electron's raw `ipcMain.on`/`ipcMain.handle` directly, so every channel is uniformly subject to the `isTrustedIPCSender` check.

## Proof of Concept
Conceptual PoC (exact reachability of a specific dangerous channel was not fully verified within the available index):
1. An attacker gets script execution in a non-trusted renderer frame inside the Desktop process (e.g., a compromised or malicious subframe rendering external/API content).
2. That frame calls `ipcRenderer.send('<channel-registered-via-raw-ipcMain-in-main.ts>', ...)` or `ipcRenderer.invoke(...)`.
3. Because the handler was registered with plain `ipcMain.on`/`ipcMain.handle` rather than the `ipc-main.ts` wrapper, `safeListener`'s `isTrustedIPCSender(event.sender)` check (`app/src/main-process/ipc-main.ts:56-62`) is never executed, and the handler runs regardless of sender trust.

**Confidence/uncertainty note:** I confirmed the structural gap — 51 raw `ipcMain.on`/`ipcMain.handle` registrations in `main.ts` versus the guarded wrappers in `ipc-main.ts` — via `grep_search`, but I was not able to enumerate within this session which of those 51 specific channels perform security-sensitive actions (vs. benign ones like window controls), since that requires reading the full body of `main.ts`. Confirming concrete high-impact exploitability would require a full audit of each channel registered there.

### Citations

**File:** app/src/main-process/trusted-ipc-sender.ts (L1-16)
```typescript
import { WebContents } from 'electron'

// WebContents id of trusted senders of IPC messages. This is used to verify
// that only IPC messages sent from trusted senders are handled, as recommended
// by the Electron security documentation:
// https://github.com/electron/electron/blob/main/docs/tutorial/security.md#17-validate-the-sender-of-all-ipc-messages
const trustedSenders = new Set<number>()

/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)
```

**File:** app/src/main-process/ipc-main.ts (L22-66)
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

**File:** app/src/main-process/app-window.ts (L1-1)
```typescript
import {
```
