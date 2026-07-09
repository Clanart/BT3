# Q380: NEAR revert_lock_actions unlock or relock asymmetry through cross-module drift

## Question
Can an unprivileged attacker use `internal callback helper reached after public settlement failures` with control over ordered list of lock or unlock actions and callback failure timing and desynchronize `near/omni-bridge/src/token_lock.rs::revert_lock_actions` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `unlock or relock asymmetry` attack class because replays inverse lock-state changes when token delivery fails after partial state changes, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::revert_lock_actions` and the adjacent lock and unlock accounting after every branch.
