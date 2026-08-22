# Q1108: AppWindow: IPC sender / webContents / origin trust failure

## Question
Can `AppWindow` in [app/src/main-process/app-window.ts] route a webContents action or IPC reply to a sender that was not the authorized one, exposing a privileged capability to untrusted content?

## Target
- File/function: [app/src/main-process/app-window.ts] — `AppWindow`
- Entrypoint: Renderer-to-main IPC, webContents routing, or the origin/webRequest filters
- Attacker controls: IPC channel and arguments, frame/sender origin, request URL/headers reaching the filter
- Exploit idea: Can `AppWindow` in [app/src/main-process/app-window.ts] route a webContents action or IPC reply to a sender that was not the authorized one, exposing a privileged capability to untrusted content?
- Invariant to test: every privileged main-process handler verifies the sender/frame/origin and rejects untrusted senders
- Expected Immunefi impact: Critical - untrusted embedded content invokes privileged main-process behaviour (file access, spawn, window/menu/updater control) (target scope: "Critical. Renderer-to-main IPC, webContents routing, or the origin/webRequest filters accept a sender, frame, or origin ...")
- Fast validation: Invoke the handler/filter in `AppWindow` with a spoofed or unexpected sender/origin in a test and assert it is rejected, not served
