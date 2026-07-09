# Q3836: NEAR callback gas budgeting one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public finalization and fast-transfer flows with user-controlled `msg`` and make `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` both release local value and create a second valid outbound bridge obligation via computes `ft_transfer_call` gas from prepaid minus used gas and falls back to strict minimum checks before minting or transferring, violating `callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers`
- Entrypoint: `public finalization and fast-transfer flows with user-controlled `msg``
- Attacker controls: message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call`
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
