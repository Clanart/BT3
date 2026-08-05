[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/account_storage_entry.rs (L194-208)
```rust
    /// Batch-insert tombstone offsets, taking the offsets lock once.
    /// Returns the number of offsets inserted.
    pub(crate) fn batch_insert_tombstone_offsets(
        &self,
        offsets: impl IntoIterator<Item = Offset>,
    ) -> usize {
        let mut tombstone_offsets = self.tombstone_offsets.write().unwrap();
        let mut num_inserted = 0;
        for offset in offsets {
            if tombstone_offsets.insert(offset) {
                num_inserted += 1;
            }
        }
        num_inserted
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L291-338)
```rust
    /// Collect the offsets that should be excluded from scans
    fn excluded_offsets(&self) -> IntSet<Offset> {
        let mut offsets: IntSet<_> = self
            .obsolete_accounts_read_lock()
            .filter_obsolete_accounts(None)
            .map(|(offset, _)| offset)
            .collect();
        offsets.extend(self.tombstone_offsets_read_lock().iter().copied());
        offsets
    }

    /// Iterate over the alive accounts in this storage, excluding obsolete accounts and tombstones.
    /// The return value is the number of values excluded from the scan.
    pub(crate) fn scan_accounts<'a>(
        &'a self,
        reader: &mut impl RequiredLenBufFileRead<'a>,
        mut callback: impl for<'local> FnMut(Offset, StoredAccountInfo<'local>),
    ) -> Result<u64, AccountsFileError> {
        let excluded_offsets = self.excluded_offsets();
        let mut num_excluded = 0;
        self.accounts.scan_accounts(reader, |offset, account| {
            if excluded_offsets.contains(&offset) {
                num_excluded += 1;
                return;
            }
            callback(offset, account);
        })?;
        Ok(num_excluded)
    }

    /// Iterate over the alive accounts in this storage without reading data, excluding obsolete
    /// accounts and tombstones. The return value is the number of values excluded from the scan.
    pub(crate) fn scan_accounts_without_data(
        &self,
        mut callback: impl for<'local> FnMut(Offset, StoredAccountInfoWithoutData<'local>),
    ) -> Result<u64, AccountsFileError> {
        let excluded_offsets = self.excluded_offsets();
        let mut num_excluded = 0;
        self.accounts
            .scan_accounts_without_data(|offset, account| {
                if excluded_offsets.contains(&offset) {
                    num_excluded += 1;
                    return;
                }
                callback(offset, account);
            })?;
        Ok(num_excluded)
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L367-431)
```rust
    #[test]
    fn test_scan_accounts_excludes_obsolete_and_tombstones() {
        let slot = 0;
        let temp_dir = TempDir::new().unwrap();
        let storage = AccountStorageEntry::new(
            temp_dir.path(),
            slot,
            0,
            1024 * 1024,
            AccountsFileProvider::AppendVec,
        );

        // Write five accounts and capture their offsets.
        let accounts: Vec<_> = iter::repeat_with(|| {
            (
                Pubkey::new_unique(),
                AccountSharedData::new(1, 10, &Pubkey::default()),
            )
        })
        .take(5)
        .collect();
        let offsets = storage
            .accounts
            .write_accounts(&(slot, &accounts[..]))
            .unwrap()
            .offsets;

        // Mark account 1 obsolete and record account 3 as a tombstone.
        let obsolete_offset = offsets[1];
        let tombstone_offset = offsets[3];
        let data_lens = storage.accounts.get_account_data_lens(&[obsolete_offset]);
        storage
            .obsolete_accounts()
            .write()
            .unwrap()
            .mark_accounts_obsolete(iter::once((obsolete_offset, data_lens[0])), slot);
        storage.batch_insert_tombstone_offsets([tombstone_offset]);

        // Scan and collect the accounts that were visited, in offset order.
        let mut reader = new_scan_accounts_reader();
        let mut visited = Vec::new();
        let num_excluded = storage
            .scan_accounts(&mut reader, |offset, account| {
                visited.push((offset, *account.pubkey()));
            })
            .unwrap();

        // Accounts 0, 2, and 4 are alive; 1 (obsolete) and 3 (tombstone) are excluded.
        assert_eq!(num_excluded, 2);
        let expected: Vec<_> = [0, 2, 4]
            .iter()
            .map(|&i| (offsets[i], accounts[i].0))
            .collect();
        assert_eq!(visited, expected);

        // scan_accounts_without_data excludes the same offsets from the same storage.
        let mut visited = Vec::new();
        let num_excluded = storage
            .scan_accounts_without_data(|offset, account| {
                visited.push((offset, *account.pubkey()));
            })
            .unwrap();
        assert_eq!(num_excluded, 2);
        assert_eq!(visited, expected);
    }
```
