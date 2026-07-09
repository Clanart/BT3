# Q1047: NEAR revert_lock_actions delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `internal callback helper reached after public settlement failures` with control over ordered list of lock or unlock actions and callback failure timing and desynchronize `near/omni-bridge/src/token_lock.rs::revert_lock_actions` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because replays inverse lock-state changes when token delivery fails after partial state changes, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::revert_lock_actions` and the adjacent lock and unlock accounting after every branch.
