### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Freezal of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that a compiled class with that hash has been declared. A contract can call `replace_class` with a non-existent class hash, causing the OS to write an invalid class hash into the contract's state entry. Any subsequent transaction that attempts to call the affected contract will cause the OS to panic during proof generation, permanently freezing all funds held by that contract.

### Finding Description

In `execute_replace_class`, the new `class_hash` is taken directly from the syscall request and written into `contract_state_changes` with no validation: [1](#0-0) 

The comment at line 898 explicitly acknowledges the missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The new `StateEntry` is written with the unvalidated `class_hash`: [2](#0-1) 

When any subsequent transaction attempts to call the affected contract, `execute_entry_point` reads the class hash from the contract state and then looks up the compiled class: [3](#0-2) 

`dict_read` on `contract_class_changes` returns `0` for an undeclared class hash (the dict default). `find_element` then searches for a compiled class with key `0`. If no such compiled class exists, `find_element` panics, making the block unprovable for any transaction touching the affected contract.

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value:
1. The corrupted state is committed to the block and proven (the `replace_class` transaction itself is valid from the OS perspective).
2. Every future transaction calling the contract causes the OS to panic at `find_element`, making those blocks unprovable.
3. The sequencer must permanently exclude all transactions targeting the contract.
4. All ERC-20 balances, NFTs, or other assets held by the contract are irrecoverably frozen with no upgrade or recovery path.

### Likelihood Explanation

Any contract that exposes a `replace_class` call path — including contracts with upgrade mechanisms, proxy patterns, or any function that accepts an arbitrary class hash from user input — can be exploited by an unprivileged transaction sender. The attacker only needs to submit a single transaction calling `replace_class` with a felt value that is not a declared class hash (e.g., `1`). No privileged access, leaked keys, or operator cooperation is required.

### Recommendation

Before writing the new class hash into `contract_state_changes`, validate that the hash exists in `contract_class_changes` (i.e., it has been declared). The check should mirror the lookup performed in `execute_entry_point`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This ensures that `replace_class` can only target a class that has a valid compiled class entry, preventing the state corruption described above.

### Proof of Concept

1. Deploy contract `C` holding funds (e.g., an ERC-20 balance).
2. From `C`, invoke the `replace_class` syscall with `class_hash = 0xdeadbeef` (any undeclared felt).
3. The OS processes `execute_replace_class`:
   - No validation is performed on `0xdeadbeef`.
   - `contract_state_changes[C].class_hash` is set to `0xdeadbeef`.
   - The block is proven successfully.
4. Submit any transaction calling `C` (e.g., a token transfer).
5. The OS executes `execute_entry_point` for `C`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0`.
   - `find_element(..., key=0)` → panics (no compiled class with hash `0`).
   - Block proof generation fails.
6. The sequencer must permanently skip all transactions targeting `C`. Funds are frozen. [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
