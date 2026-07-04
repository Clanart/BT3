### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract actually corresponds to a declared class. This is an explicitly acknowledged missing check (marked with a TODO). Any contract can call `replace_class` with an arbitrary, undeclared class hash. Once committed, the contract's class hash in the state tree is permanently set to a value for which no executable class exists, making the contract permanently uncallable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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
``` [1](#0-0) 

The function reads `request.class_hash` directly from the syscall request and writes it into `contract_state_changes` without any lookup into `contract_class_changes` to confirm the class was ever declared. The TODO comment at line 898 explicitly acknowledges this missing validation. [2](#0-1) 

By contrast, `execute_declare_transaction` in `transaction_impls.cairo` does enforce that a class hash corresponds to a valid Sierra class by calling `finalize_class_hash` and asserting the result matches:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
``` [3](#0-2) 

And it enforces uniqueness via `dict_update` with `prev_value=0`:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [4](#0-3) 

These two operations — class declaration and class replacement — are entirely decoupled. The OS enforces validity only at declaration time, not at replacement time. This is the direct analog of the original report's pattern: the "transfer" (class declaration) and the "state recording" (class replacement) are separate steps with no atomicity enforcement between them.

---

### Impact Explanation

When a contract calls `replace_class` with a class hash that has never been declared:

1. The OS writes the invalid class hash into `contract_state_changes` and ultimately into the committed state tree via `compute_contract_state_commitment`.
2. In all subsequent blocks, any transaction targeting that contract will cause the OS to look up the invalid class hash. Since no entry point exists for that hash, execution cannot proceed.
3. The contract becomes permanently uncallable. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path.

This matches the allowed impact: **Critical — Permanent freezing of funds.** [5](#0-4) 

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly accessible syscall available to any Sierra contract. No privileged role is required. A realistic attack path:

1. A malicious actor deploys a contract (e.g., a fake vault, pool, or bridge) that accepts user deposits.
2. After accumulating funds, the contract internally calls `replace_class` with an arbitrary felt value (e.g., `1`) that was never declared.
3. The OS accepts the call without validation and commits the invalid class hash to state.
4. All deposited funds are permanently frozen.

Additionally, legitimate contracts with upgrade mechanisms that contain a bug in their class hash selection logic are silently bricked with no OS-level safety net, because the OS performs no existence check.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the class hash exists in `contract_class_changes` (i.e., has been declared in the current block) or in the existing contract class commitment tree. This is exactly what the existing TODO comment calls for:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The fix should perform a `dict_read` on `contract_class_changes` for the given `class_hash` and assert the result is non-zero (i.e., a compiled class hash was registered), or alternatively perform a Merkle proof check against the current class tree root, before allowing the state update to proceed. [6](#0-5) 

---

### Proof of Concept

1. Attacker deploys contract `VaultAttack` with class hash `C_valid` (a legitimately declared class).
2. Users deposit funds into `VaultAttack`.
3. `VaultAttack` internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (never declared).
4. `execute_replace_class` in `syscall_impls.cairo` (line 896–910) reads `class_hash = 0xdeadbeef` from the request, skips the missing existence check (line 898 TODO), and calls `dict_update` to write `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
5. The block is proven and committed. The state tree now records `VaultAttack → class_hash=0xdeadbeef`.
6. In the next block, any user attempting to withdraw calls `VaultAttack`. The OS reads `class_hash=0xdeadbeef` from state, finds no entry point, and the transaction reverts.
7. The class hash in state remains `0xdeadbeef` permanently. All deposited funds are frozen with no recovery mechanism.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L76-111)
```text
func compute_contract_state_commitment{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_state_changes_start: DictAccess*,
    n_contract_state_changes: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> CommitmentUpdate {
    alloc_locals;

    // Hash the entries of the contract state changes to prepare the input for the commitment tree
    // multi-update.
    let (local hashed_state_changes: DictAccess*) = alloc();
    compute_contract_state_commitment_inner(
        state_changes=contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        hashed_state_changes=hashed_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the contracts' state tree.
    local initial_root;
    local final_root;

    %{ SetPreimageForStateCommitments %}

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_state_changes,
        n_updates=n_contract_state_changes,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (CommitmentUpdate(initial_root=initial_root, final_root=final_root));
}
```
