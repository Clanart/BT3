[1](#0-0)

### Citations

**File:** rpc/src/rpc_completed_slots_service.rs (L44-53)
```rust
                        Ok(slots) => {
                            for slot in slots {
                                rpc_subscriptions.notify_slot_update(SlotUpdate::Completed {
                                    slot,
                                    timestamp: timestamp(),
                                });
                                if let Some(slot_status_notifier) = &slot_status_notifier {
                                    slot_status_notifier.read().unwrap().notify_completed(slot);
                                }
                            }
```
