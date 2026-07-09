# Q1213: NEAR revert_lock_actions delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `internal callback helper reached after public settlement failures` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::revert_lock_actions` violate `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction` in the `delivery callback leaves inconsistent state` attack class because replays inverse lock-state changes when token delivery fails after partial state changes becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
