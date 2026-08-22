# Q4837: for: untrusted repository/API text escapes render sandbox to a privileged capability

## Question
Does `for` in [app/src/lib/wrap-rich-text-commit-message.ts] emit an attacker-controlled URL or href without scheme allow-listing, letting a `javascript:`/`file:`/custom-protocol link execute or navigate when the user interacts with rendered content?

## Target
- File/function: [app/src/lib/wrap-rich-text-commit-message.ts] — `for`
- Entrypoint: Markdown, commit message, PR/issue title, branch name, avatar/image URL, diff body, or Actions log rendered in the renderer
- Attacker controls: the untrusted string content (markdown, title, ref name, URL, log bytes) shown in the UI
- Exploit idea: Does `for` in [app/src/lib/wrap-rich-text-commit-message.ts] emit an attacker-controlled URL or href without scheme allow-listing, letting a `javascript:`/`file:`/custom-protocol link execute or navigate when the user interacts with rendered content?
- Invariant to test: untrusted rendered text can never reach a privileged capability or execute as markup/script or a dangerous URL scheme
- Expected Immunefi impact: Critical - sandbox/renderer escape reaching IPC, `shell.openExternal`, node APIs, or arbitrary navigation (target scope: "Critical. Untrusted repository or API text - markdown, commit message, PR or issue title, branch name, avatar or image U...")
- Fast validation: Render a payload string through `for` in a test and assert markup/`javascript:`/`file:`/protocol handlers are neutralized, not emitted or invoked
