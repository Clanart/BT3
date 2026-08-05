No vulnerability found for this question.

**Analysis:**

`modify_memory_region_of_account` in `program-runtime/src/serialization.rs` performs no loaded-accounts-data-size (or any other) accounting whatsoever. It simply redirects a `MemoryRegion`'s host buffer pointer to the account's *current* data buffer, sized from `account.get_data().len()` read at call time: [1](#0-0) 

It is invoked both during initial serialization (`Serializer::write_account`) and after CPI returns (`update_caller_account_region`), and in both call sites the account length used is whatever the account's *current* state is at that moment — never a stale/cached value: [2](#0-1) [3](#0-2) 

The invariant the question asks about (loaded/committed data size tracking) is enforced by two entirely separate, independent mechanisms, neither of which this function touches:

1. **Per-transaction resize delta** — every data-mutating path on `BorrowedInstructionAccount` (`set_data_length`, `set_data_from_slice`, `extend_from_slice`) unconditionally calls `update_accounts_resize_delta` before applying the change, and `can_data_be_resized` enforces the cap against `MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION` prior to any mutation: [4](#0-3) [5](#0-4) 

2. **Loaded-accounts-data-size limit (compute budget)** — computed independently at load time in `svm/src/account_loader.rs` via `LoadedTransactionDataSize::increase_calculated_data_size`, based on real account sizes fetched from the loader (including programdata dedup logic), and enforced against `requested_loaded_accounts_data_size_limit`: [6](#0-5) 

Duplicate writable aliases don't introduce extra untracked buffers either — the serializer dedups by index and reuses metadata from the first occurrence (`SerializeAccount::Duplicate`), so there is exactly one underlying data buffer per unique account regardless of how many instruction-account slots alias it: [7](#0-6) 

Since `modify_memory_region_of_account` never counts or caches sizes — it only re-points a memory region to the account's live buffer using its live length — there is no path by which it can cause an "undercount" relative to the real memory footprint. The resize-delta and loaded-data-size invariants are maintained by other, already-audited code paths that apply on every mutation, independent of this function.

### Citations

**File:** program-runtime/src/serialization.rs (L22-53)
```rust
/// Modifies the memory mapping in serialization and CPI return for virtual_address_space_adjustments
pub fn modify_memory_region_of_account(
    account: &mut BorrowedInstructionAccount<'_, '_>,
    region: &mut MemoryRegion,
) {
    let data_ptr = region.host_buffer().ptr() as *mut u8;
    let new_buffer = std::ptr::slice_from_raw_parts_mut(data_ptr, account.get_data().len());
    if account.can_data_be_changed().is_ok() {
        unsafe {
            // SAFETY:
            // Contract from `MemoryRegion::redirect`: The memory pointed to by the MemoryRegions
            // must point to a valid object live for the duration of this MemoryMapping.
            //
            // TODO(nagisa): Local reasoning for this contract is infeasible. In particular for the
            // `serialization.rs` code it is pretty easy to see that the regions passed in will
            // always be larger than `account.get_data().len()`. However for `cpi.rs` callsite this
            // is not as easy to prove and relies on careful coordination between any code that
            // might increase the account data buffer length.
            region.redirect(new_buffer);
        }
        region.access_violation_handler_payload = Some(account.get_index_in_transaction());
    } else {
        unsafe {
            // SAFETY:
            //
            // Contract from `MemoryRegion::redirect`: same as for the call above.
            // Evidence: same as for the call above.
            region.redirect(new_buffer.cast_const());
        }
        region.access_violation_handler_payload = None;
    }
}
```

**File:** program-runtime/src/serialization.rs (L178-188)
```rust
            if address_space_reserved_for_account > 0 {
                if !self.account_data_direct_mapping {
                    self.push_region();
                    let region = self.regions.last_mut().unwrap();
                    modify_memory_region_of_account(account, region);
                } else {
                    let new_region = create_memory_region_of_account(account, self.vaddr)?;
                    self.vaddr += address_space_reserved_for_account as u64;
                    self.regions.push(new_region);
                }
            }
```

**File:** program-runtime/src/serialization.rs (L386-414)
```rust
    for account in accounts {
        match account {
            SerializeAccount::Duplicate(position) => {
                accounts_metadata.push(accounts_metadata.get(position as usize).unwrap().clone());
                s.write(position as u8);
            }
            SerializeAccount::Account(_, mut account) => {
                let vm_addr = s.write::<u8>(NON_DUP_MARKER);
                s.write::<u8>(account.is_signer() as u8);
                s.write::<u8>(account.is_writable() as u8);
                let vm_key_addr = s.write_all(account.get_key().as_ref());
                let vm_lamports_addr = s.write::<u64>(account.get_lamports().to_le());
                s.write::<u64>((account.get_data().len() as u64).to_le());
                let vm_data_addr = s.write_account(&mut account)?;
                let vm_owner_addr = s.write_all(account.get_owner().as_ref());
                #[expect(deprecated)]
                s.write::<u8>(account.is_executable() as u8);
                let rent_epoch = u64::MAX;
                s.write::<u64>(rent_epoch.to_le());
                accounts_metadata.push(SerializedAccountMetadata {
                    vm_addr,
                    original_data_len: account.get_data().len(),
                    vm_key_addr,
                    vm_lamports_addr,
                    vm_owner_addr,
                    vm_data_addr,
                });
            }
        };
```

**File:** program-runtime/src/cpi.rs (L1179-1218)
```rust
unsafe fn update_caller_account_region(
    memory_mapping: &mut MemoryMapping,
    check_aligned: bool,
    caller_account: &CallerAccount,
    callee_account: &mut BorrowedInstructionAccount<'_, '_>,
    account_data_direct_mapping: bool,
) -> Result<(), Error> {
    let is_caller_loader_deprecated = !check_aligned;
    let address_space_reserved_for_account = if is_caller_loader_deprecated {
        caller_account.original_data_len
    } else {
        caller_account
            .original_data_len
            .saturating_add(MAX_PERMITTED_DATA_INCREASE)
    };

    if address_space_reserved_for_account > 0 {
        // We can trust vm_data_addr to point to the correct region because we
        // enforce that in CallerAccount::from_(sol_)account_info.
        let (region_index, region) = memory_mapping
            .find_region(caller_account.vm_data_addr)
            .ok_or_else(|| Box::new(InstructionError::MissingAccount) as Error)?;
        // vm_data_addr must always point to the beginning of the region
        let region_start_vm_addr = region.vm_addr_range().start;
        debug_assert_eq!(region_start_vm_addr, caller_account.vm_data_addr);
        let mut new_region;
        if !account_data_direct_mapping {
            new_region = region.clone();
            modify_memory_region_of_account(callee_account, &mut new_region);
        } else {
            new_region = create_memory_region_of_account(callee_account, region_start_vm_addr)?;
        }
        unsafe {
            // SAFETY: the lifetime invariants are delegated to the callers of this function. Both
            // `modify_memory_region_of_account` and `create_memory_region_of_account` create memory
            // regions pointing to valid buffers by the virtue of the region being produced out of
            // an intermediate slice, which itself must be wholly valid.
            memory_mapping.replace_region(region_index, new_region)?;
        }
    }
```

**File:** transaction-context/src/instruction_accounts.rs (L181-207)
```rust
    pub fn set_data_from_slice(&mut self, data: &[u8]) -> Result<(), InstructionError> {
        self.can_data_be_resized(data.len())?;
        self.touch()?;
        self.update_accounts_resize_delta(data.len())?;
        // Note that we intentionally don't call self.make_data_mut() here.  make_data_mut() will
        // allocate + memcpy the current data if self.account is shared. We don't need the memcpy
        // here tho because account.set_data_from_slice(data) is going to replace the content
        // anyway.
        self.account.set_data_from_slice(data);

        Ok(())
    }

    /// Resizes the account data (transaction wide)
    ///
    /// Fills it with zeros at the end if is extended or truncates at the end otherwise.
    pub fn set_data_length(&mut self, new_length: usize) -> Result<(), InstructionError> {
        self.can_data_be_resized(new_length)?;
        // don't touch the account if the length does not change
        if self.get_data().len() == new_length {
            return Ok(());
        }
        self.touch()?;
        self.update_accounts_resize_delta(new_length)?;
        self.account.resize(new_length, 0);
        Ok(())
    }
```

**File:** transaction-context/src/transaction_accounts.rs (L297-326)
```rust
    pub(crate) fn update_accounts_resize_delta(
        &self,
        old_len: usize,
        new_len: usize,
    ) -> Result<(), InstructionError> {
        let accounts_resize_delta = self.resize_delta.get();
        self.resize_delta.set(
            accounts_resize_delta.saturating_add((new_len as i64).saturating_sub(old_len as i64)),
        );
        Ok(())
    }

    pub(crate) fn can_data_be_resized(
        &self,
        old_len: usize,
        new_len: usize,
    ) -> Result<(), InstructionError> {
        // The new length can not exceed the maximum permitted length
        if new_len > MAX_ACCOUNT_DATA_LEN as usize {
            return Err(InstructionError::InvalidRealloc);
        }
        // The resize can not exceed the per-transaction maximum
        let length_delta = (new_len as i64).saturating_sub(old_len as i64);
        if self.resize_delta.get().saturating_add(length_delta)
            > MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION
        {
            return Err(InstructionError::MaxAccountsDataAllocationsExceeded);
        }
        Ok(())
    }
```

**File:** svm/src/account_loader.rs (L474-520)
```rust
#[derive(PartialEq, Eq, Debug, Clone)]
struct LoadedTransactionDataSize {
    loaded_accounts_data_size: u32,
    requested_loaded_accounts_data_size_limit: u32,
}

impl LoadedTransactionDataSize {
    fn with_max_size(requested_loaded_accounts_data_size_limit: u32) -> Self {
        Self {
            loaded_accounts_data_size: 0,
            requested_loaded_accounts_data_size_limit,
        }
    }

    fn increase_calculated_data_size(
        &mut self,
        data_size_delta: usize,
        error_metrics: &mut TransactionErrorMetrics,
    ) -> Result<()> {
        // this branch is unreachable in practice (though not by construction),
        // since it would imply an account >4gb in size
        let Ok(data_size_delta) = u32::try_from(data_size_delta) else {
            self.loaded_accounts_data_size = u32::MAX;
            error_metrics.max_loaded_accounts_data_size_exceeded += 1;
            return Err(TransactionError::MaxLoadedAccountsDataSizeExceeded);
        };

        self.loaded_accounts_data_size = self
            .loaded_accounts_data_size
            .saturating_add(data_size_delta);

        if self.loaded_accounts_data_size > self.requested_loaded_accounts_data_size_limit {
            error_metrics.max_loaded_accounts_data_size_exceeded += 1;
            Err(TransactionError::MaxLoadedAccountsDataSizeExceeded)
        } else {
            Ok(())
        }
    }
}

impl From<LoadedTransactionDataSize> for u32 {
    fn from(value: LoadedTransactionDataSize) -> Self {
        value
            .loaded_accounts_data_size
            .min(value.requested_loaded_accounts_data_size_limit)
    }
}
```
