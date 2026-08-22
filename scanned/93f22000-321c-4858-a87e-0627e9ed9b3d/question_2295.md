# Q2295: installWindowsCLI: updater / installer / CLI-install payload substitution

## Question
Can `installWindowsCLI` in [app/src/main-process/squirrel-updater.ts] accept an update-feed or install response without verifying integrity/source, letting an attacker-controlled response install substituted executable content?

## Target
- File/function: [app/src/main-process/squirrel-updater.ts] — `installWindowsCLI`
- Entrypoint: The auto-update, Squirrel installer, or CLI-install resolution/verification/write path
- Attacker controls: update feed response, on-disk payload location, symlink/permissions of the install target
- Exploit idea: Can `installWindowsCLI` in [app/src/main-process/squirrel-updater.ts] accept an update-feed or install response without verifying integrity/source, letting an attacker-controlled response install substituted executable content?
- Invariant to test: the updater/installer only executes content whose source and integrity it verified and whose write path an unprivileged user cannot pre-empt
- Expected Immunefi impact: Critical - substituted executable content runs on next launch or CLI invocation (target scope: "Critical. The auto-update, Squirrel installer, or CLI-install path resolves, verifies, or writes its payload so that an ...")
- Fast validation: Simulate an unprivileged-writable path or unverified feed response into `installWindowsCLI` in a test and assert the payload is rejected before execution
