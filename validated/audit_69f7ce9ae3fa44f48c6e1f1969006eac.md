### Title
Missing Validation of Replacement Class Hash in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

`execute_replace_class` in the StarkNet OS does not verify that the caller-supplied `class_hash` corresponds to a previously declared class. An attacker controlling a contract can invoke the `replace_class` syscall with an arbitrary, undeclared class hash, permanently setting the contract's class to a non-existent one. Any funds held by that contract become permanently frozen.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without checking whether that hash exists in `contract_class_changes` (the declared-class registry):

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    ...
}
```

The developer-inserted TODO at line 898 explicitly acknowledges the missing check. The `class_hash` field in the `ReplaceClassRequest` is fully attacker-controlled — it is read from the syscall segment written by the executing contract, with no constraint imposed by the OS Cairo code.

This is the direct analog of the reported vulnerability: just as `EscrowManager.createLock()` only checks `beneficiary()` but not the wallet address itself, `execute_replace_class` only checks gas availability but not whether the supplied class hash is a legitimately declared class. [1](#0-0) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class` succeeds with an arbitrary, undeclared hash:

1. `contract_state_changes` records the contract's new `class_hash` as the attacker-chosen value.
2. `compute_contract_state_commitment` hashes this entry into the Patricia Merkle Tree without any cross-check against `contract_class_changes`.
3. The state root committed on-chain reflects a contract whose class does not exist in the class tree.
4. Every subsequent call to that contract will fail at class-lookup time — the OS cannot find bytecode for the phantom class hash.
5. All ERC-20 tokens, NFTs, or other assets held in the contract's storage are permanently inaccessible. [2](#0-1) [3](#0-2) 

---

### Likelihood Explanation

**High.** The `replace_class` syscall is a standard, publicly documented StarkNet syscall reachable by any deployed contract. No privileged role, leaked key, or operator cooperation is required. An attacker needs only to:

1. Deploy a contract (or control an existing one) that emits a `replace_class` syscall with an arbitrary felt as the class hash.
2. Submit the transaction — the OS will process it without rejection.

The explicit TODO comment confirms the development team is aware the check is absent, meaning no defense-in-depth currently compensates for it. [4](#0-3) 

---

### Recommendation

Before writing the new `StateEntry`, verify that `request.class_hash` has a non-zero entry in `contract_class_changes`:

```cairo
let class_hash = request.class_hash;

// Validate that the class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
if (compiled_class_hash == UNINITIALIZED_CLASS_HASH) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
```

This mirrors the pattern already used in `execute_declare_transaction`, where `assert_not_zero(compiled_class_hash)` enforces that a valid compiled class hash exists before committing a class declaration. [5](#0-4) 

---

### Proof of Concept

1. **Attacker deploys** a contract `Attacker` whose `__execute__` function emits a `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary, never-declared felt).
2. **Attacker submits** an invoke transaction targeting `Attacker.__execute__`.
3. **OS processes** the transaction: `execute_syscalls` dispatches to `execute_replace_class`; the function reads `request.class_hash = 0xdeadbeef`, skips any class-existence check (the TODO), and calls `dict_update` on `contract_state_changes` with the phantom hash.
4. **State commitment**: `compute_contract_state_commitment` hashes the `StateEntry` containing `class_hash=0xdeadbeef` into the Patricia tree. No cross-check against `contract_class_changes` occurs.
5. **Block is finalized** with the corrupted state root on-chain.
6. **Any future call** to `Attacker` (or any victim contract that was targeted via a delegated `replace_class`) fails permanently — the OS cannot resolve `0xdeadbeef` to any compiled class. All funds in the contract are frozen forever. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L114-141)
```text
func compute_contract_state_commitment_inner{hash_ptr: HashBuiltin*, range_check_ptr}(
    state_changes: DictAccess*,
    n_contract_state_changes: felt,
    hashed_state_changes: DictAccess*,
    patricia_update_constants: PatriciaUpdateConstants*,
) {
    if (n_contract_state_changes == 0) {
        return ();
    }
    alloc_locals;

    // Compute the previous and new hash of the contract state and write the result into
    // hashed_state_changes[0].
    hash_contract_state_changes(
        contract_address=state_changes.key,
        prev_state=cast(state_changes.prev_value, StateEntry*),
        new_state=cast(state_changes.new_value, StateEntry*),
        patricia_update_constants=patricia_update_constants,
        hashed_state_changes=&hashed_state_changes[0],
    );

    return compute_contract_state_commitment_inner(
        state_changes=&state_changes[1],
        n_contract_state_changes=n_contract_state_changes - 1,
        hashed_state_changes=&hashed_state_changes[1],
        patricia_update_constants=patricia_update_constants,
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L148-205)
```text
func hash_contract_state_changes{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_address: felt,
    prev_state: StateEntry*,
    new_state: StateEntry*,
    patricia_update_constants: PatriciaUpdateConstants*,
    hashed_state_changes: DictAccess*,
) {
    alloc_locals;

    local initial_contract_state_root;
    local final_contract_state_root;

    %{ SetPreimageForCurrentCommitmentInfo %}

    local state_dict_start: DictAccess* = prev_state.storage_ptr;
    local state_dict_end: DictAccess* = new_state.storage_ptr;
    local n_updates = (state_dict_end - state_dict_start) / DictAccess.SIZE;
    // Call patricia_update_using_update_constants() (or the read-optimized variant) instead of
    // patricia_update() in order not to repeat globals_pow2 calculation.
    local should_use_read_optimized: felt;
    %{ ShouldUseReadOptimizedPatriciaUpdate %}
    if (should_use_read_optimized != 0) {
        patricia_update_read_optimized(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    } else {
        patricia_update_using_update_constants(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    }
    local range_check_ptr = range_check_ptr;

    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
    assert hashed_state_changes.prev_value = prev_value;
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
