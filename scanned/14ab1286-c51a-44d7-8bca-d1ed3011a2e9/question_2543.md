# Q2543: CloseKeywordFilter: untrusted repository/API text escapes render sandbox to a privileged capability

## Question
Can a crafted string flowing through `CloseKeywordFilter` in [app/src/lib/markdown-filters/close-keyword-filter.ts] break out of the sandboxed-markdown/render escaping and invoke `shell.openExternal`, IPC, or node APIs from otherwise-untrusted content?

## Target
- File/function: [app/src/lib/markdown-filters/close-keyword-filter.ts] — `CloseKeywordFilter`
- Entrypoint: Markdown, commit message, PR/issue title, branch name, avatar/image URL, diff body, or Actions log rendered in the renderer
- Attacker controls: the untrusted string content (markdown, title, ref name, URL, log bytes) shown in the UI
- Exploit idea: Can a crafted string flowing through `CloseKeywordFilter` in [app/src/lib/markdown-filters/close-keyword-filter.ts] break out of the sandboxed-markdown/render escaping and invoke `shell.openExternal`, IPC, or node APIs from otherwise-untrusted content?
- Invariant to test: untrusted rendered text can never reach a privileged capability or execute as markup/script or a dangerous URL scheme
- Expected Immunefi impact: Critical - sandbox/renderer escape reaching IPC, `shell.openExternal`, node APIs, or arbitrary navigation (target scope: "Critical. Untrusted repository or API text - markdown, commit message, PR or issue title, branch name, avatar or image U...")
- Fast validation: Render a payload string through `CloseKeywordFilter` in a test and assert markup/`javascript:`/`file:`/protocol handlers are neutralized, not emitted or invoked
