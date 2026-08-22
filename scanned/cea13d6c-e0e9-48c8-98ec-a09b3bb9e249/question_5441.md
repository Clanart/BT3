# Q5441: removeExistingSymlink: updater / installer / CLI-install payload substitution

## Question
Can an on-path attacker or unprivileged local user substitute the payload that `removeExistingSymlink` in [app/src/ui/lib/install-cli.ts] resolves, verifies, or writes, so attacker-chosen content executes on the next launch or CLI invocation?

## Target
- File/function: [app/src/ui/lib/install-cli.ts] — `removeExistingSymlink`
- Entrypoint: The auto-update, Squirrel installer, or CLI-install resolution/verification/write path
- Attacker controls: update feed response, on-disk payload location, symlink/permissions of the install target
- Exploit idea: Can an on-path attacker or unprivileged local user substitute the payload that `removeExistingSymlink` in [app/src/ui/lib/install-cli.ts] resolves, verifies, or writes, so attacker-chosen content executes on the next launch or CLI invocation?
- Invariant to test: the updater/installer only executes content whose source and integrity it verified and whose write path an unprivileged user cannot pre-empt
- Expected Immunefi impact: Critical - substituted executable content runs on next launch or CLI invocation (target scope: "Critical. The auto-update, Squirrel installer, or CLI-install path resolves, verifies, or writes its payload so that an ...")
- Fast validation: Simulate an unprivileged-writable path or unverified feed response into `removeExistingSymlink` in a test and assert the payload is rejected before execution
