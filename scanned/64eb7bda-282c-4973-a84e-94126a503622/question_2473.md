# Q2473: NEAR revert_lock_actions cleanup order around callbacks reopens or strands value at boundary values

## Question
Can an unprivileged attacker trigger `internal callback helper reached after public settlement failures` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::revert_lock_actions` violate `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction` in the `cleanup order around callbacks reopens or strands value` attack class because replays inverse lock-state changes when token delivery fails after partial state changes becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Focus on removal of pending records, finalization flags, and lock rollback relative to payout callbacks. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Inject failures at each callback boundary and assert that cleanup always leaves one consistent recoverable state. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
