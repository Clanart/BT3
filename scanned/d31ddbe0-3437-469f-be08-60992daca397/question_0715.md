# Q715: NEAR revert_lock_actions delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `internal callback helper reached after public settlement failures` that causes `near/omni-bridge/src/token_lock.rs::revert_lock_actions` to keep or remove settlement state inconsistently with delivered value because of replays inverse lock-state changes when token delivery fails after partial state changes, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
