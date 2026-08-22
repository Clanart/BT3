# Q0668: suppressCertificateErrorFor: file read/transmit outside repository scope -> disclosure

## Question
Does `suppressCertificateErrorFor` in [app/src/lib/suppress-certificate-error.ts] follow an attacker-planted path/symlink so content outside the repo is transmitted into an error report, telemetry, or model context reachable by the attacker?

## Target
- File/function: [app/src/lib/suppress-certificate-error.ts] — `suppressCertificateErrorFor`
- Entrypoint: Attacker-controlled repository content or API data driving a diff, commit, Copilot/conflict context, or error report
- Attacker controls: repository file paths, diff/conflict inputs, content fed into Copilot or crash/error reporting
- Exploit idea: Does `suppressCertificateErrorFor` in [app/src/lib/suppress-certificate-error.ts] follow an attacker-planted path/symlink so content outside the repo is transmitted into an error report, telemetry, or model context reachable by the attacker?
- Invariant to test: only files inside the selected repository scope are read into a diff, commit, Copilot context, or transmitted report
- Expected Immunefi impact: High - local user files outside the repository are read, staged, or transmitted to the attacker (target scope: "High. Attacker-controlled repository content or API data makes Desktop read, stage, or transmit files outside the select...")
- Fast validation: Point `suppressCertificateErrorFor` at an out-of-repo or symlinked path in a test and assert it is excluded from the read/transmitted set
