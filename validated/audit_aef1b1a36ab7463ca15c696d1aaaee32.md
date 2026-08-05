[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/replay_stage.rs (L2668-2680)
```rust
            for (confirmed_slot, duplicate_confirmed_hash) in new_duplicate_confirmed_slots {
                if confirmed_slot <= root {
                    continue;
                } else if let Some(prev_hash) =
                    duplicate_confirmed_slots.insert(confirmed_slot, duplicate_confirmed_hash)
                {
                    // This assertion is intentional - it is not possible to split the cluster to get 52% on two versions
                    // without a massive turbine failure
                    assert_eq!(
                        prev_hash, duplicate_confirmed_hash,
                        "Additional duplicate confirmed notification for slot {confirmed_slot} \
                         with a different hash"
                    );
```

**File:** core/src/cluster_info_vote_listener.rs (L1-1)
```rust
use {
```
