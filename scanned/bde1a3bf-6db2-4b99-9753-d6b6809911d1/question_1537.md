# Q1537: NEAR revert_lock_actions different callback outcomes produce the same user-visible success via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal callback helper reached after public settlement failures` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/token_lock.rs::revert_lock_actions` ends up accepting two inconsistent interpretations of the same economic event specifically around `different callback outcomes produce the same user-visible success` under replays inverse lock-state changes when token delivery fails after partial state changes, violating `rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::revert_lock_actions`
- Entrypoint: `internal callback helper reached after public settlement failures`
- Attacker controls: ordered list of lock or unlock actions and callback failure timing
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: rollback must restore the exact pre-call lock state even if actions repeat, collapse, or fail in the opposite direction
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
