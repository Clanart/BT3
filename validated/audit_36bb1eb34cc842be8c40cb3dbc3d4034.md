Based on the investigation, I found a strong structural analog to the reported bug class (state changes made to an in-memory/working copy that are not reliably propagated back to the authoritative persisted state).

### Title
Native/CPI account mutations via `try_borrow_mut()` may bypass `touched_flags` tracking, causing modified accounts to be silently dropped from bank write-back - (File: `transaction-context/src/transaction_accounts.rs`, `runtime/src/account_saver.rs`)

### Summary
Agave tracks, per transaction, which loaded accounts were actually modified using a `touched_flags: Box<[Cell<bool>]>` array on `TransactionAccounts` [1](#0-0) . At the end of execution these flags gate whether an account is written back to the bank at all: `collect_accounts_for_successful_tx` explicitly skips any writable account whose flag is unset, on the assumption that it was left unmodified [2](#0-1) . This is architecturally identical to the LienToken pattern: an in-memory working copy (`stack`, here the account's mutable view) can be updated, but a separate bookkeeping value (`touched_flags`, `s.lienMeta` in the original) must be kept in sync manually, and if a mutation path forgets to do so, the change never reaches the persisted store.

### Finding Description
The only call site setting a touched flag is inside `TransactionContext::access_violation_handler`, which fires when the **BPF VM** itself writes into guest memory backing an account and explicitly calls `accounts.touch(index_in_transaction)` before performing the write [3](#0-2) .

However, `TransactionAccounts::try_borrow_mut()` — the lower-level API used to obtain a mutable account handle for CPI account synchronization and for builtin/native programs — does **not** itself set the touched flag [4](#0-3) . Its `Drop` impl only reconciles the shared/private field lengths and releases the borrow counter; it never calls `touch()` [5](#0-4) .

Concretely, CPI's `update_callee_account()` mutates the callee's lamports, data, and owner directly through this borrowed-account API (`callee_account.set_lamports(...)`, `set_data_from_slice(...)`, `set_owner(...)`) without any adjacent call to mark the account touched [6](#0-5) . A repo-wide search for calls to `.touch(` only surfaces the single call site in `transaction.rs`'s VM access-violation handler — no matches exist in `program-runtime/src/cpi.rs`, `instruction.rs`/`instruction_accounts.rs`, or the native/builtin program call machinery. This means any account state change performed purely through the "logical"/CPI-return path (rather than a raw guest-VM memory write caught by the access-violation handler) has no code path guaranteeing `touched_flags[i]` gets set for that index.

Since `collect_accounts_for_successful_tx` unconditionally skips any writable account with `touched_flags[i] == false` [7](#0-6) , an account whose lamports/data/owner were changed exclusively via such a path would be silently excluded from `collect_accounts_to_store`'s output and never written into the bank's accounts-db, even though `TransactionExecutionDetails::status` reports `Ok(())` for the transaction.

### Impact Explanation
If a genuine code path exists where account mutation occurs without ever passing through the VM access-violation handler (i.e., without a BPF program directly touching its own guest-mapped memory for that account — for example builtin/native program mutations, or CPI-only updates where the callee side sets state via `update_callee_account` without a corresponding guest memory write), the effect is: a transaction is marked successful, fees/state are logged as applied, but the actual account state change is never persisted to the bank. This is a "false execution acceptance" class bug — the ledger/bank state diverges from what the transaction's reported outcome implies. Because lamports and data changes are involved, this can directly translate into fund loss (a transfer that appears to succeed but silently reverts) or acceptance of an invalid resulting state.

Since the bug is deterministic and lives in shared protocol code, it is not itself a source of validator disagreement (all validators run the same code and would compute the same, wrong, outcome), so it is unlikely to cause a raw consensus halt/fork by itself. Its primary danger is silently incorrect execution results being universally (and consistently) accepted by the network.

### Likelihood Explanation
This is difficult to conclusively confirm without directly inspecting the concrete call sites in `instruction.rs`/`instruction_accounts.rs` that expose `BorrowedInstructionAccount`'s setters (`set_lamports`, `set_owner`, `set_data_from_slice`, etc.) to see whether they wrap `try_borrow_mut()` and independently invoke `TransactionAccounts::touch()` elsewhere (e.g., via a different method name not caught by the `touch(` grep, or via the instruction-processing wrapper that finalizes accounts after every instruction). I was not able to view those files within the available tool budget, so I cannot rule out that touched-flag setting happens through an indirect mechanism (e.g., all writable instruction accounts for an executed instruction being marked touched in bulk by the instruction processor, regardless of whether the VM access-violation handler fired). If such a blanket "mark all writable instruction accounts touched after every instruction" mechanism exists elsewhere in the instruction execution pipeline, this finding would be moot. This uncertainty should be treated as the primary open question before treating this as a confirmed, exploitable bug.

### Recommendation
- Verify whether `BorrowedInstructionAccount`/`InstructionContext` finalization logic (in `transaction-context/src/instruction.rs` or `instruction_accounts.rs`) unconditionally calls `TransactionAccounts::touch()` for every writable instruction account after each instruction executes, independent of whether guest VM memory was touched.
- If no such guarantee exists, add an explicit `accounts.touch(index)` call inside every mutating setter path reachable through `try_borrow_mut()` (or centralize touching in `AccountRefMut::Drop`, mirroring the recommendation from the original report to fold state updates into a single authoritative, storage-backed location rather than relying on scattered call sites to keep memory and persisted state in sync).
- Add a regression test that performs an account mutation exclusively through a builtin/native program (not through direct BPF program memory writes) and asserts the mutated data is present in the bank's accounts-db after transaction execution.

### Proof of Concept
Could not be fully constructed without confirming the exact instruction-finalization code path (see Likelihood Explanation). The conceptual PoC would be: build/execute a transaction whose sole writable-account mutation is performed by a builtin program that operates purely through `TransactionAccounts::try_borrow_mut()`/`BorrowedInstructionAccount` setters without triggering the `access_violation_handler` (i.e., no BPF program directly writes to the account's mapped guest memory region). If `touched_flags` for that account index remains `false` after execution, `collect_accounts_to_store` will omit it, and `bank.get_account()` after the transaction will show the pre-transaction state despite the transaction status reporting success.

### Citations

**File:** transaction-context/src/transaction_accounts.rs (L234-241)
```rust
pub struct TransactionAccounts {
    shared_account_fields: Box<[UnsafeCell<AccountSharedFields>]>,
    private_account_fields: Box<[UnsafeCell<AccountPrivateFields>]>,
    borrow_counters: Box<[BorrowCounter]>,
    touched_flags: Box<[Cell<bool>]>,
    resize_delta: Cell<i64>,
    lamports_delta: Cell<i128>,
}
```

**File:** transaction-context/src/transaction_accounts.rs (L329-367)
```rust
    pub(crate) fn try_borrow_mut(
        &self,
        index: IndexOfAccount,
    ) -> Result<AccountRefMut<'_>, InstructionError> {
        let borrow_counter = self
            .borrow_counters
            .get(index as usize)
            .ok_or(InstructionError::MissingAccount)?;
        borrow_counter.try_borrow_mut()?;

        // SAFETY: The borrow counter guarantees this is the only mutable borrow of this account.
        // The unwrap is safe because accounts.len() == borrow_counters.len(), so the missing
        // account error should have been returned above.
        let svm_account = unsafe {
            &mut *self
                .shared_account_fields
                .get(index as usize)
                .unwrap()
                .get()
        };

        let private_fields = unsafe {
            &mut *self
                .private_account_fields
                .get(index as usize)
                .unwrap()
                .get()
        };

        let account = TransactionAccountViewMut {
            abi_account: svm_account,
            private_fields,
        };

        Ok(AccountRefMut {
            account,
            borrow_counter,
        })
    }
```

**File:** transaction-context/src/transaction_accounts.rs (L573-582)
```rust
#[cfg(not(any(target_arch = "bpf", target_arch = "sbf")))]
impl Drop for AccountRefMut<'_> {
    fn drop(&mut self) {
        self.account
            .abi_account
            .payload
            .set_len(self.account.private_fields.payload_len() as u64);
        self.borrow_counter.release_borrow_mut();
    }
}
```

**File:** runtime/src/account_saver.rs (L117-125)
```rust
    for (i, (address, account)) in (0..transaction.account_keys().len()).zip(transaction_accounts) {
        if !transaction.is_writable(i) {
            continue;
        }

        // Skip write-locked accounts the transaction left unmodified.
        if !touched_flags[i] {
            continue;
        }
```

**File:** transaction-context/src/transaction.rs (L550-557)
```rust
                let Ok(mut account) = accounts.try_borrow_mut(index_in_transaction) else {
                    debug_assert!(false);
                    return;
                };
                if accounts.touch(index_in_transaction).is_err() {
                    debug_assert!(false);
                    return;
                }
```

**File:** program-runtime/src/cpi.rs (L1108-1172)
```rust
fn update_callee_account(
    memory_mapping: &MemoryMapping,
    check_aligned: bool,
    caller_account: &CallerAccount,
    mut callee_account: BorrowedInstructionAccount<'_, '_>,
    syscall_parameter_address_restrictions: bool,
    virtual_address_space_adjustments: bool,
    account_data_direct_mapping: bool,
) -> Result<bool, Error> {
    let mut must_update_caller = false;

    if callee_account.get_lamports() != *caller_account.lamports {
        callee_account.set_lamports(*caller_account.lamports)?;
    }

    if virtual_address_space_adjustments {
        let prev_len = callee_account.get_data().len();
        let post_len = *caller_account.ref_to_len_in_vm as usize;
        if prev_len != post_len {
            if !account_data_direct_mapping && post_len < prev_len {
                // If the account has been shrunk, we're going to zero the unused memory
                // *that was previously used*.
                let serialized_data = unsafe {
                    CallerAccount::get_serialized_data(
                        memory_mapping,
                        check_aligned,
                        caller_account.vm_data_addr,
                        caller_account.original_data_len,
                        prev_len,
                        syscall_parameter_address_restrictions,
                        virtual_address_space_adjustments,
                        account_data_direct_mapping,
                    )?
                };
                serialized_data
                    .get_mut(post_len..)
                    .ok_or_else(|| Box::new(InstructionError::AccountDataTooSmall) as Error)?
                    .fill(0);
            }
            callee_account.set_data_length(post_len)?;
            // pointer to data may have changed, so caller must be updated
            must_update_caller = true;
        }
        if !account_data_direct_mapping && callee_account.can_data_be_changed().is_ok() {
            callee_account.set_data_from_slice(caller_account.serialized_data)?;
        }
    } else {
        // The redundant check helps to avoid the expensive data comparison if we can
        match callee_account.can_data_be_resized(caller_account.serialized_data.len()) {
            Ok(()) => callee_account.set_data_from_slice(caller_account.serialized_data)?,
            Err(err) if callee_account.get_data() != caller_account.serialized_data => {
                return Err(Box::new(err));
            }
            _ => {}
        }
    }

    // Change the owner at the end so that we are allowed to change the lamports and data before
    if callee_account.get_owner() != caller_account.owner {
        callee_account.set_owner(caller_account.owner.as_ref())?;
        // caller gave ownership and thus write access away, so caller must be updated
        must_update_caller = true;
    }

    Ok(must_update_caller)
```
