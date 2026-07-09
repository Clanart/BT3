# Q2017: NEAR revert_lock_actions cleanup order around callbacks reopens or strands value

## Question
Can an unprivileged attacker trigger `internal callback helper reached after public settlement failures` so that `near/omni-bridge/src/token_lock.rs::revert_lock_actions` cleans up transfer or fast-transfer state in an order that either reopens replay or strands user funds after callback failure, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Focus on removal of pending records, finalization flags, and lock rollback relative to payout callbacks.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Inject failures at each callback boundary and assert that cleanup always leaves one consistent recoverable state.
