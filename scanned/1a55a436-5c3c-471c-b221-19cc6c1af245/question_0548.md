# Q548: NEAR revert_lock_actions unlock or relock asymmetry at boundary values

## Question
Can an unprivileged attacker trigger `internal callback helper reached after public settlement failures` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::revert_lock_actions` violate `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction` in the `unlock or relock asymmetry` attack class because replays inverse lock-state changes when token delivery fails after partial state changes becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
