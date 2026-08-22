# Q1107: installAliveOriginFilter: IPC sender / webContents / origin trust failure

## Question
Can `installAliveOriginFilter` in [app/src/main-process/alive-origin-filter.ts] route a webContents action or IPC reply to a sender that was not the authorized one, exposing a privileged capability to untrusted content?

## Target
- File/function: [app/src/main-process/alive-origin-filter.ts] — `installAliveOriginFilter`
- Entrypoint: Renderer-to-main IPC, webContents routing, or the origin/webRequest filters
- Attacker controls: IPC channel and arguments, frame/sender origin, request URL/headers reaching the filter
- Exploit idea: Can `installAliveOriginFilter` in [app/src/main-process/alive-origin-filter.ts] route a webContents action or IPC reply to a sender that was not the authorized one, exposing a privileged capability to untrusted content?
- Invariant to test: every privileged main-process handler verifies the sender/frame/origin and rejects untrusted senders
- Expected Immunefi impact: Critical - untrusted embedded content invokes privileged main-process behaviour (file access, spawn, window/menu/updater control) (target scope: "Critical. Renderer-to-main IPC, webContents routing, or the origin/webRequest filters accept a sender, frame, or origin ...")
- Fast validation: Invoke the handler/filter in `installAliveOriginFilter` with a spoofed or unexpected sender/origin in a test and assert it is rejected, not served
