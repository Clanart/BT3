# Q5626: handleUpdated: updater / installer / CLI-install payload substitution

## Question
Does `handleUpdated` in [app/src/main-process/squirrel-updater.ts] write or resolve the update/CLI-install target through a location an unprivileged user can pre-create or redirect (symlink, world-writable dir), enabling code execution?

## Target
- File/function: [app/src/main-process/squirrel-updater.ts] — `handleUpdated`
- Entrypoint: The auto-update, Squirrel installer, or CLI-install resolution/verification/write path
- Attacker controls: update feed response, on-disk payload location, symlink/permissions of the install target
- Exploit idea: Does `handleUpdated` in [app/src/main-process/squirrel-updater.ts] write or resolve the update/CLI-install target through a location an unprivileged user can pre-create or redirect (symlink, world-writable dir), enabling code execution?
- Invariant to test: the updater/installer only executes content whose source and integrity it verified and whose write path an unprivileged user cannot pre-empt
- Expected Immunefi impact: Critical - substituted executable content runs on next launch or CLI invocation (target scope: "Critical. The auto-update, Squirrel installer, or CLI-install path resolves, verifies, or writes its payload so that an ...")
- Fast validation: Simulate an unprivileged-writable path or unverified feed response into `handleUpdated` in a test and assert the payload is rejected before execution
