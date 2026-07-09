# Q1376: NEAR revert_lock_actions different callback outcomes produce the same user-visible success

## Question
Can an unprivileged attacker use `internal callback helper reached after public settlement failures` so that `near/omni-bridge/src/token_lock.rs::revert_lock_actions` treats materially different callback outcomes as the same economic result because of replays inverse lock-state changes when token delivery fails after partial state changes, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition.
