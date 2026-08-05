[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** accounts-db/src/account_storage.rs (L33-72)
```rust
impl AccountStorage {
    /// Return the append vec in 'slot' and with id='store_id'.
    /// can look in 'map' and 'shrink_in_progress_map' to find the specified append vec
    /// when shrinking begins, shrinking_in_progress is called.
    /// This fn looks in 'map' first, then in 'shrink_in_progress_map', then in 'map' again because
    /// 'shrinking_in_progress' first inserts the new append vec into 'shrink_in_progress_map'
    /// Then, when 'shrink_in_progress' is dropped,
    /// the old append vec is replaced in 'map' with the new append vec
    /// then the new append vec is dropped from 'shrink_in_progress_map'.
    /// So, it is possible for a race with this fn and dropping 'shrink_in_progress'.
    /// Callers to this function have 2 choices:
    /// 1. hold the account index read lock for the pubkey so that the account index entry cannot be changed prior to or during this call. (scans do this)
    /// 2. expect to be ready to start over and read the index again if this function returns None
    ///
    /// Operations like shrinking or write cache flushing may have updated the index between when the caller read the index and called this function to
    /// load from the append vec specified in the index.
    ///
    /// In practice, this fn will return the entry from the map in the very first lookup unless a shrink is in progress.
    /// The third lookup will only be called if a requesting thread exactly interposes itself between the 2 map manipulations in the drop of 'shrink_in_progress'.
    pub(crate) fn get_account_storage_entry(
        &self,
        slot: Slot,
        store_id: AccountsFileId,
    ) -> Option<Arc<AccountStorageEntry>> {
        let lookup_in_map = || {
            self.map.get(&slot).and_then(|entry| {
                (entry.value().id() == store_id).then_some(Arc::clone(entry.value()))
            })
        };

        lookup_in_map()
            .or_else(|| {
                self.shrink_in_progress_map
                    .read()
                    .unwrap()
                    .get(&slot)
                    .and_then(|entry| (entry.id() == store_id).then(|| Arc::clone(entry)))
            })
            .or_else(lookup_in_map)
    }
```

**File:** accounts-db/src/account_storage.rs (L79-80)
```rust
    /// return the append vec for 'slot' if it exists
    /// This is only ever called when shrink is not possibly running and there is a max of 1 append vec per slot.
```

**File:** accounts-db/src/account_storage.rs (L81-88)
```rust
    pub fn get_slot_storage_entry(&self, slot: Slot) -> Option<Arc<AccountStorageEntry>> {
        assert!(
            self.no_shrink_in_progress(),
            "shrink is in progress! slots: {:?}",
            self.shrink_in_progress_map.read().unwrap().keys(),
        );
        self.get_slot_storage_entry_shrinking_in_progress_ok(slot)
    }
```

**File:** accounts-db/src/account_storage.rs (L611-622)
```rust
    #[test]
    #[should_panic(expected = "shrink is in progress!")]
    fn test_get_slot_storage_entry_fail() {
        let (_temp_dir, sample) = new_test_storage();
        let storage = AccountStorage::default();
        storage
            .shrink_in_progress_map
            .write()
            .unwrap()
            .insert(0, sample);
        storage.get_slot_storage_entry(0);
    }
```
