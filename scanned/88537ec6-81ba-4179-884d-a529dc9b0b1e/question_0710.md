# Q0710: OrderedWebRequest: IPC sender / webContents / origin trust failure

## Question
Does the origin/webRequest filter in `OrderedWebRequest` in [app/src/main-process/ordered-webrequest.ts] accept a frame or request origin it should reject, allowing attacker-loaded content to pass same-origin or allow-list checks?

## Target
- File/function: [app/src/main-process/ordered-webrequest.ts] — `OrderedWebRequest`
- Entrypoint: Renderer-to-main IPC, webContents routing, or the origin/webRequest filters
- Attacker controls: IPC channel and arguments, frame/sender origin, request URL/headers reaching the filter
- Exploit idea: Does the origin/webRequest filter in `OrderedWebRequest` in [app/src/main-process/ordered-webrequest.ts] accept a frame or request origin it should reject, allowing attacker-loaded content to pass same-origin or allow-list checks?
- Invariant to test: every privileged main-process handler verifies the sender/frame/origin and rejects untrusted senders
- Expected Immunefi impact: Critical - untrusted embedded content invokes privileged main-process behaviour (file access, spawn, window/menu/updater control) (target scope: "Critical. Renderer-to-main IPC, webContents routing, or the origin/webRequest filters accept a sender, frame, or origin ...")
- Fast validation: Invoke the handler/filter in `OrderedWebRequest` with a spoofed or unexpected sender/origin in a test and assert it is rejected, not served
