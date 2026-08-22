# Q0996: formatError: file read/transmit outside repository scope -> disclosure

## Question
Can a crafted repository path reaching `formatError` in [app/src/lib/logging/format-error.ts] widen the file set read or sent beyond the repo, exposing files the user never intended to share?

## Target
- File/function: [app/src/lib/logging/format-error.ts] — `formatError`
- Entrypoint: Attacker-controlled repository content or API data driving a diff, commit, Copilot/conflict context, or error report
- Attacker controls: repository file paths, diff/conflict inputs, content fed into Copilot or crash/error reporting
- Exploit idea: Can a crafted repository path reaching `formatError` in [app/src/lib/logging/format-error.ts] widen the file set read or sent beyond the repo, exposing files the user never intended to share?
- Invariant to test: only files inside the selected repository scope are read into a diff, commit, Copilot context, or transmitted report
- Expected Immunefi impact: High - local user files outside the repository are read, staged, or transmitted to the attacker (target scope: "High. Attacker-controlled repository content or API data makes Desktop read, stage, or transmit files outside the select...")
- Fast validation: Point `formatError` at an out-of-repo or symlinked path in a test and assert it is excluded from the read/transmitted set
