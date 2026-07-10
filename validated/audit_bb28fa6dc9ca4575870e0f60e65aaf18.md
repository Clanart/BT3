### Title
Silent Lock Skip in `lock_tokens` Enables Permanent Fund Freeze After `locked_tokens` Entry Initialization — (File: near/omni-bridge/src/token_lock.rs)

---

### Summary

`lock_tokens` silently returns `LockAction::Unchanged` when no entry exists in the `locked_tokens` map for a given `(chain_kind, token_id)` key. If a