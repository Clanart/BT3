# Q212: NEAR revert_lock_actions unlock or relock asymmetry via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal callback helper reached after public settlement failures` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/token_lock.rs::revert_lock_actions` ends up accepting two inconsistent interpretations of the same economic event specifically around `unlock or relock asymmetry` under replays inverse lock-state changes when token delivery fails after partial state changes, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
