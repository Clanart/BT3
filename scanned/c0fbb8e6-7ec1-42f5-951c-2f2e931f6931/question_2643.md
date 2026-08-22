# Q2643: resolve: file read/transmit outside repository scope -> disclosure

## Question
Can attacker-controlled repository content make `resolve` in [app/src/main-process/exception-reporting.ts] read or include a file outside the selected repository scope into a diff, commit, or Copilot/conflict context, disclosing local user data?

## Target
- File/function: [app/src/main-process/exception-reporting.ts] — `resolve`
- Entrypoint: Attacker-controlled repository content or API data driving a diff, commit, Copilot/conflict context, or error report
- Attacker controls: repository file paths, diff/conflict inputs, content fed into Copilot or crash/error reporting
- Exploit idea: Can attacker-controlled repository content make `resolve` in [app/src/main-process/exception-reporting.ts] read or include a file outside the selected repository scope into a diff, commit, or Copilot/conflict context, disclosing local user data?
- Invariant to test: only files inside the selected repository scope are read into a diff, commit, Copilot context, or transmitted report
- Expected Immunefi impact: High - local user files outside the repository are read, staged, or transmitted to the attacker (target scope: "High. Attacker-controlled repository content or API data makes Desktop read, stage, or transmit files outside the select...")
- Fast validation: Point `resolve` at an out-of-repo or symlinked path in a test and assert it is excluded from the read/transmitted set
