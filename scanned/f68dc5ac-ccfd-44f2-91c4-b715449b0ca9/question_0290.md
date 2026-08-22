# Q0290: now: IPC sender / webContents / origin trust failure

## Question
Can untrusted embedded content invoke `now` in [app/src/main-process/now.ts] over IPC without the main process verifying the sender/frame, letting it reach privileged file, spawn, window, or updater behaviour?

## Target
- File/function: [app/src/main-process/now.ts] — `now`
- Entrypoint: Renderer-to-main IPC, webContents routing, or the origin/webRequest filters
- Attacker controls: IPC channel and arguments, frame/sender origin, request URL/headers reaching the filter
- Exploit idea: Can untrusted embedded content invoke `now` in [app/src/main-process/now.ts] over IPC without the main process verifying the sender/frame, letting it reach privileged file, spawn, window, or updater behaviour?
- Invariant to test: every privileged main-process handler verifies the sender/frame/origin and rejects untrusted senders
- Expected Immunefi impact: Critical - untrusted embedded content invokes privileged main-process behaviour (file access, spawn, window/menu/updater control) (target scope: "Critical. Renderer-to-main IPC, webContents routing, or the origin/webRequest filters accept a sender, frame, or origin ...")
- Fast validation: Invoke the handler/filter in `now` with a spoofed or unexpected sender/origin in a test and assert it is rejected, not served
