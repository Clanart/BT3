# Q832: NEAR callback gas budgeting resume-path replay or duplication

## Question
Can an unprivileged attacker make the deferred path behind `public finalization and fast-transfer flows with user-controlled `msg`` resume more than once or resume after the economic transfer was already completed because `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` relies on computes `ft_transfer_call` gas from prepaid minus used gas and falls back to strict minimum checks before minting or transferring, violating `callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers`
- Entrypoint: `public finalization and fast-transfer flows with user-controlled `msg``
- Attacker controls: message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call`
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed.
- Invariant to test: callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once.
