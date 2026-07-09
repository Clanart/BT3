# Q1859: NEAR revert_lock_actions different callback outcomes produce the same user-visible success at boundary values

## Question
Can an unprivileged attacker trigger `internal callback helper reached after public settlement failures` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::revert_lock_actions` violate `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction` in the `different callback outcomes produce the same user-visible success` attack class because replays inverse lock-state changes when token delivery fails after partial state changes becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
