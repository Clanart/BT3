# Q3371: KnownUserHit: untrusted repository/API text escapes render sandbox to a privileged capability

## Question
Can attacker-controlled repository or API text (commit message, PR/issue title, branch name, log line) passed to `KnownUserHit` in [app/src/ui/autocompletion/user-autocompletion-provider.tsx] escape sanitization and inject markup or a dangerous URL that reaches a privileged capability?

## Target
- File/function: [app/src/ui/autocompletion/user-autocompletion-provider.tsx] — `KnownUserHit`
- Entrypoint: Markdown, commit message, PR/issue title, branch name, avatar/image URL, diff body, or Actions log rendered in the renderer
- Attacker controls: the untrusted string content (markdown, title, ref name, URL, log bytes) shown in the UI
- Exploit idea: Can attacker-controlled repository or API text (commit message, PR/issue title, branch name, log line) passed to `KnownUserHit` in [app/src/ui/autocompletion/user-autocompletion-provider.tsx] escape sanitization and inject markup or a dangerous URL that reaches a privileged capability?
- Invariant to test: untrusted rendered text can never reach a privileged capability or execute as markup/script or a dangerous URL scheme
- Expected Immunefi impact: Critical - sandbox/renderer escape reaching IPC, `shell.openExternal`, node APIs, or arbitrary navigation (target scope: "Critical. Untrusted repository or API text - markdown, commit message, PR or issue title, branch name, avatar or image U...")
- Fast validation: Render a payload string through `KnownUserHit` in a test and assert markup/`javascript:`/`file:`/protocol handlers are neutralized, not emitted or invoked
