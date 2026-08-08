### Title
Missing bound check on attacker-controlled `len` in `CallerAccount::get_serialized_data` when `syscall_parameter_address_restrictions` is inactive but `virtual_address_space_adjustments` is active, yielding an out-of-bounds mutable slice - (File: program-runtime/src/cpi.rs)

### Summary
`CallerAccount::get_serialized_data` only performs the `len > original_data_len + MAX_PERMITTED_DATA_INCREASE` bound check when `syscall_parameter_address_restrictions` is `true`. When that flag is `false` but `virtual_address_space_adjustments` is `true`, the function skips the bound check entirely and instead builds a raw slice with `std::slice::from_raw_parts_mut` using an attacker-supplied `len`, derived directly from the caller-controlled `AccountInfo::data` length field, with no validation against the actual mapped region size.

### Finding Description
In `CallerAccount::get_serialized_data` [1](#0-0) , the length bound check is gated entirely by `if syscall_parameter_address_restrictions { ... }`. If that boolean is `false`, no check on `len` occurs before it is used.

When `virtual_address_space_adjustments` is `true` (and `account_data_direct_mapping` is `false`), the function takes the "workaround" branch: [2](#0-1) 
It translates only a 1-byte slice at `MM_INPUT_START` to obtain a valid host pointer, then constructs `std::slice::from_raw_parts_mut(ptr.add(vm_addr - MM_INPUT_START), len)` with the caller-supplied `len` — this call performs no bounds checking against the mapped region's actual extent.

The `len` value fed into this function from `CallerAccount::from_account_info` is: [3](#0-2) 
```
if syscall_parameter_address_restrictions {
    *ref_to_len_in_vm as usize
} else {
    data.len()
}
```
When `syscall_parameter_address_restrictions` is `false`, `len = data.len()`, which comes directly from the `AccountInfo::data` fat-pointer length field that lives in guest (VM) memory the attacker's own program controls. There is no re-check of this value against `original_data_len + MAX_PERMITTED_DATA_INCREASE` anywhere else in this call path when restrictions are off — that check exists only inside `get_serialized_data`'s `if syscall_parameter_address_restrictions` block.

The same unchecked pattern applies in `update_caller_account`, which also calls `get_serialized_data` with `post_len` from the callee's actual data length [4](#0-3) , and in `update_callee_account` [5](#0-4) , both of which pass through the same feature flags.

### Feasibility caveat
This exploit requires the specific runtime feature combination `syscall_parameter_address_restrictions = false` AND `virtual_address_space_adjustments = true`. Feature activation is controlled by validator supermajority governance vote, not by an unprivileged attacker, so the attacker cannot toggle this combination themselves. All test coverage found in the repository (`programs/sbf/tests/programs.rs` at lines 2635-2644, 3916-3929, 4543-4552) always toggles `syscall_parameter_address_restrictions`, `virtual_address_space_adjustments`, and `account_data_direct_mapping` together as a group — never independently — strongly suggesting these three SIMD features (SIMD-0459, SIMD-0460, and direct mapping) are intended to be activated/deactivated as a coordinated set on mainnet-beta, never in this specific split state. No source-code assertion enforces this dependency, but no operational precedent for the split state was found either.

### Impact Explanation
If this exact split feature state were ever reached, the resulting `std::slice::from_raw_parts_mut` call with unchecked attacker-controlled `len` would produce a mutable slice extending past the true bounds of the VM input memory region, enabling out-of-bounds reads/writes in the host validator process from guest-controlled data — a guest sandbox escape (Critical, out-of-bounds memory access via CPI).

### Likelihood Explanation
Low/unproven in practice: the required precondition (this exact feature-flag combination active simultaneously on a live cluster) has not been demonstrated to be reachable — feature activation is coordinated by validator governance and code/test evidence indicates these three flags are always toggled together, never independently, in this codebase's lifecycle. Absent evidence that mainnet-beta, testnet, or devnet ever holds `syscall_parameter_address_restrictions=false` and `virtual_address_space_adjustments=true` simultaneously, this is a code-level latent hazard contingent on an operational/governance precondition rather than a directly attacker-triggerable bug.

### Recommendation
Move the length bound check in `get_serialized_data` out of the `if syscall_parameter_address_restrictions` gate so it always applies regardless of that flag, or add an explicit assertion/invariant in feature-set construction that `virtual_address_space_adjustments` cannot be active while `syscall_parameter_address_restrictions` is inactive, preventing this split state from ever existing.

### Proof of Concept
```rust
// program-runtime/src/cpi.rs test module
#[test]
fn test_get_serialized_data_oob_when_restrictions_off_but_vasa_on() {
    let transaction_accounts =
        transaction_with_one_writable_instruction_account(b"foo".to_vec());
    let account = transaction_accounts[1].1.clone();
    mock_invoke_context!(
        invoke_context, transaction_context, b"instruction data",
        transaction_accounts, 0, &[1]
    );

    let config = Config { aligned_memory_mapping: false, ..Config::default() };
    let memory_mapping =
        unsafe { MemoryMapping::new(vec![], &config, SBPFVersion::V3).unwrap() };

    let oversized_len = account.data().len()
        .saturating_add(MAX_PERMITTED_DATA_INCREASE)
        .saturating_add(1);

    let serialized_data = unsafe {
        CallerAccount::get_serialized_data(
            &memory_mapping,
            true,
            MM_INPUT_START,
            account.data().len(),
            oversized_len,
            false, // syscall_parameter_address_restrictions OFF
            true,  // virtual_address_space_adjustments ON
            false, // account_data_direct_mapping
        )
    };

    // Expected (per invariant): should still be rejected with InvalidRealloc
    // or a bounds error. Actual observed behavior: bypasses the check and
    // attempts to build an oversized raw slice via from_raw_parts_mut,
    // exposing memory beyond the mapped region instead of erroring.
    assert_matches!(
        serialized_data,
        Err(error) if error.downcast_ref::<InstructionError>() == Some(&InstructionError::InvalidRealloc)
    );
}
```
Note: this test targets the isolated function's contract; demonstrating full end-to-end exploitability additionally requires proving that the described feature-flag split state (`syscall_parameter_address_restrictions=false`, `virtual_address_space_adjustments=true`) is actually reachable on a live cluster, which was not established from the available code/context.

### Citations

**File:** program-runtime/src/cpi.rs (L216-238)
```rust
    pub unsafe fn get_serialized_data(
        memory_mapping: &solana_sbpf::memory_region::MemoryMapping,
        check_aligned: bool,
        vm_addr: u64,
        original_data_len: usize,
        len: usize,
        syscall_parameter_address_restrictions: bool,
        virtual_address_space_adjustments: bool,
        account_data_direct_mapping: bool,
    ) -> Result<&'a mut [u8], Error> {
        use crate::memory::translate_slice_mut_for_cpi;

        if syscall_parameter_address_restrictions {
            let is_caller_loader_deprecated = !check_aligned;
            let address_space_reserved_for_account = if is_caller_loader_deprecated {
                original_data_len
            } else {
                original_data_len.saturating_add(MAX_PERMITTED_DATA_INCREASE)
            };
            if len > address_space_reserved_for_account {
                return Err(InstructionError::InvalidRealloc.into());
            }
        }
```

**File:** program-runtime/src/cpi.rs (L241-257)
```rust
        } else if virtual_address_space_adjustments {
            // Workaround the memory permissions (as these are from the PoV of being inside the VM)
            unsafe {
                // SAFETY: Invariants for constructing a mutable reference delegated to the caller.
                let serialization_ptr: &'a mut [u8] = translate_slice_mut_for_cpi::<u8>(
                    memory_mapping,
                    solana_sbpf::ebpf::MM_INPUT_START,
                    1,
                    false, // Don't care since it is byte aligned
                )?;
                Ok(std::slice::from_raw_parts_mut(
                    serialization_ptr
                        .as_mut_ptr()
                        .add(vm_addr.saturating_sub(solana_sbpf::ebpf::MM_INPUT_START) as usize),
                    len,
                ))
            }
```

**File:** program-runtime/src/cpi.rs (L378-393)
```rust
            let serialized_data = unsafe {
                CallerAccount::get_serialized_data(
                    memory_mapping,
                    check_aligned,
                    vm_data_addr,
                    account_metadata.original_data_len,
                    if syscall_parameter_address_restrictions {
                        *ref_to_len_in_vm as usize
                    } else {
                        data.len()
                    },
                    syscall_parameter_address_restrictions,
                    virtual_address_space_adjustments,
                    account_data_direct_mapping,
                )?
            };
```

**File:** program-runtime/src/cpi.rs (L1123-1146)
```rust
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
```

**File:** program-runtime/src/cpi.rs (L1235-1297)
```rust
fn update_caller_account(
    invoke_context: &InvokeContext,
    check_aligned: bool,
    caller_account: &mut CallerAccount<'_>,
    callee_account: &mut BorrowedInstructionAccount<'_, '_>,
    syscall_parameter_address_restrictions: bool,
    virtual_address_space_adjustments: bool,
    account_data_direct_mapping: bool,
) -> Result<(), Error> {
    *caller_account.lamports = callee_account.get_lamports();
    *caller_account.owner = *callee_account.get_owner();

    let prev_len = *caller_account.ref_to_len_in_vm as usize;
    let post_len = callee_account.get_data().len();
    let is_caller_loader_deprecated = !check_aligned;
    let address_space_reserved_for_account =
        if syscall_parameter_address_restrictions && is_caller_loader_deprecated {
            caller_account.original_data_len
        } else {
            caller_account
                .original_data_len
                .saturating_add(MAX_PERMITTED_DATA_INCREASE)
        };

    if post_len > address_space_reserved_for_account
        && (syscall_parameter_address_restrictions || prev_len != post_len)
    {
        let max_increase =
            address_space_reserved_for_account.saturating_sub(caller_account.original_data_len);
        ic_msg!(
            invoke_context,
            "Account data size realloc limited to {max_increase} in inner instructions",
        );
        return Err(Box::new(InstructionError::InvalidRealloc));
    }

    let memory_mapping = invoke_context.memory_contexts.memory_mapping()?;
    if prev_len != post_len {
        // when virtual_address_space_adjustments is enabled we don't cache the serialized data in
        // caller_account.serialized_data. See CallerAccount::from_account_info.
        if !(virtual_address_space_adjustments && account_data_direct_mapping) {
            // If the account has been shrunk, we're going to zero the unused memory
            // *that was previously used*.
            if post_len < prev_len {
                caller_account
                    .serialized_data
                    .get_mut(post_len..)
                    .ok_or_else(|| Box::new(InstructionError::AccountDataTooSmall) as Error)?
                    .fill(0);
            }
            // Set the length of caller_account.serialized_data to post_len.
            unsafe {
                caller_account.serialized_data = CallerAccount::get_serialized_data(
                    memory_mapping,
                    check_aligned,
                    caller_account.vm_data_addr,
                    caller_account.original_data_len,
                    post_len,
                    syscall_parameter_address_restrictions,
                    virtual_address_space_adjustments,
                    account_data_direct_mapping,
                )?;
            }
```
