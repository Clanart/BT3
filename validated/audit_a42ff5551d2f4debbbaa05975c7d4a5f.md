### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value from a contract without verifying that the new hash corresponds to a declared class. This is directly analogous to the external report's finding: state-changing functions proceed without validating their inputs. A contract can call `replace_class` with `class_hash = 0` or any undeclared hash, permanently corrupting its own state entry. Any funds held by that contract become permanently inaccessible.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `request.class_hash` and immediately writes it into `contract_state_changes` without any check that the value corresponds to a class that has been declared on-chain:

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

The development team's own TODO comment at line 898 explicitly acknowledges the missing check. [1](#0-0) 

After the state is committed with an invalid `class_hash`, any subsequent call to that contract reaches `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // now 0 or undeclared
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // 0 → not found → panic
);
```

`find_element` panics when the key is absent, making the contract permanently uncallable. [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is overwritten with `0` or any undeclared hash:

- Every future call to that contract causes `find_element` to panic inside the OS prover, making the contract permanently uncallable.
- All ERC-20 balances, NFTs, or other assets stored in that contract's storage become permanently inaccessible with no recovery path.
- The state commitment is already finalized with the corrupted class hash, so there is no on-chain mechanism to undo it.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself. The triggering scenarios include:

1. A contract with a logic bug that passes an unvalidated user-supplied value to `replace_class`.
2. A contract intentionally designed to self-destruct (e.g., a honeypot or griefing tool).
3. An upgrade mechanism that fails to validate the new class hash before calling `replace_class`.

Because the OS is the last line of defense and performs no validation, any of these paths permanently bricks the contract. The explicit TODO comment confirms the team is aware the check is absent.

---

### Recommendation

Before updating `contract_state_changes`, verify that `class_hash` is non-zero and exists in `contract_class_changes` (i.e., it has been declared):

```cairo
// Ensure the new class hash is non-zero.
assert_not_zero(class_hash);

// Ensure the new class hash has been declared (has a compiled class entry).
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already present in `execute_declare_transaction` at line 816: `assert_not_zero(compiled_class_hash)`. [3](#0-2) 

---

### Proof of Concept

1. Deploy a contract whose `__execute__` entry point calls the `replace_class` syscall with `class_hash = 0`.
2. Submit an invoke transaction targeting that entry point.
3. The OS processes `execute_replace_class`: no validation fires, `contract_state_changes` is updated with `class_hash = 0` for the contract address. [4](#0-3) 
4. The block is proven and committed; the contract's state entry now holds `class_hash = 0`.
5. Any subsequent transaction calling the contract reaches `execute_entry_point`, which calls `dict_read` with key `0`, gets compiled class hash `0`, then calls `find_element` with key `0`. Since no compiled class with hash `0` exists, `find_element` panics and proof generation fails for that call. [2](#0-1) 
6. The contract is permanently uncallable; all funds it holds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
