# Q1549: UNSAFE_openDirectory: IPC sender / webContents / origin trust failure

## Question
Can crafted arguments to the IPC handler `UNSAFE_openDirectory` in [app/src/main-process/shell.ts] bypass its sender/trust validation and drive a main-process action the renderer should not be able to request?

## Target
- File/function: [app/src/main-process/shell.ts] — `UNSAFE_openDirectory`
- Entrypoint: Renderer-to-main IPC, webContents routing, or the origin/webRequest filters
- Attacker controls: IPC channel and arguments, frame/sender origin, request URL/headers reaching the filter
- Exploit idea: Can crafted arguments to the IPC handler `UNSAFE_openDirectory` in [app/src/main-process/shell.ts] bypass its sender/trust validation and drive a main-process action the renderer should not be able to request?
- Invariant to test: every privileged main-process handler verifies the sender/frame/origin and rejects untrusted senders
- Expected Immunefi impact: Critical - untrusted embedded content invokes privileged main-process behaviour (file access, spawn, window/menu/updater control) (target scope: "Critical. Renderer-to-main IPC, webContents routing, or the origin/webRequest filters accept a sender, frame, or origin ...")
- Fast validation: Invoke the handler/filter in `UNSAFE_openDirectory` with a spoofed or unexpected sender/origin in a test and assert it is rejected, not served
