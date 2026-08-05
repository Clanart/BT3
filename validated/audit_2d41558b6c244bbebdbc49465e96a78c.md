[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L404-429)
```rust
    pub(crate) fn slot_list_mut_with_entry<RT>(
        &self,
        pubkey: &Pubkey,
        user_fn: impl FnOnce(SlotListWriteGuard<T>, &AccountMapEntry<T>) -> RT,
    ) -> Option<RT> {
        let mut write_through_args: Option<(Slot, T)> = None;
        let result = self.get_internal_inner(pubkey, |entry| {
            (
                true,
                entry.map(|entry| {
                    let result = user_fn(entry.slot_list_write_lock(), entry);
                    // always mark dirty unconditionally, even if user_fn made no changes
                    entry.mark_dirty();
                    if self.should_write_through && entry.ref_count() == 1 {
                        let slot_list = entry.slot_list_read_lock();
                        if slot_list.len() == 1 {
                            write_through_args = Some(slot_list[0]);
                        }
                    }
                    result
                }),
            )
        });
        if let Some((slot, account_info)) = write_through_args {
            self.write_through(pubkey, slot, account_info);
        }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L454-463)
```rust
    /// Write `(slot, account_info)` to the disk index, then under the slot list read lock
    /// verify the in-mem entry still matches; if so, clear the dirty flag so the entry
    /// is eligible for eviction without waiting for the background flush.
    ///
    /// We hold the slot list read lock during the equality check to prevent concurrent
    /// modifications from invalidating our check between the disk write and the dirty-clear.
    /// Any concurrent upsert that modifies the slot list must hold the write lock, so it
    /// cannot proceed until we release. If it ran before us the check will fail and we leave
    /// the entry dirty for the next write to clean up; if it runs after, it will re-dirty
    /// the now-clean entry and call write_through itself.
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L470-480)
```rust
        self.get_only_in_mem(pubkey, false, |entry| {
            if let Some(entry) = entry {
                let slot_list = entry.slot_list_read_lock();
                if slot_list.len() == 1
                    && slot_list[0] == (slot, account_info)
                    && entry.ref_count() == 1
                {
                    entry.clear_dirty();
                }
            }
        });
```

**File:** accounts-db/src/accounts_index/account_map_entry.rs (L28-28)
```rust
    slot_list: RwLock<SlotList<T>>,
```

**File:** accounts-db/src/accounts_index/account_map_entry.rs (L129-139)
```rust
    pub fn slot_list_read_lock(&self) -> SlotListReadGuard<'_, T> {
        SlotListReadGuard(self.slot_list.read().unwrap())
    }

    /// Acquire a write lock on the slot list and return accessor for modifying it
    ///
    /// Do not call any locking function (`slot_list_*lock*`) on the same `AccountMapEntry` until accessor
    /// they return is dropped.
    pub fn slot_list_write_lock(&self) -> SlotListWriteGuard<'_, T> {
        SlotListWriteGuard(self.slot_list.write().unwrap())
    }
```
