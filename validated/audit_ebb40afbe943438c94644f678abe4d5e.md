### Title
Missing Declared-Class Validation in `replace_class` Syscall Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared contract class. An unprivileged contract can invoke the `replace_class` syscall with a fabricated, undeclared class hash, permanently bricking itself and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), after deducting gas, the OS reads `request.class_hash` directly from the syscall request and writes it unconditionally into `contract_state_changes` via `dict_update`:

```cairo
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
```

The TODO comment at line 898 explicitly acknowledges the missing check. No assertion is made that `class_hash` exists in `contract_class_changes` (the declared-class mapping) or in `compiled_class_facts_bundle` (the set of compiled classes available to the OS for execution).

Contrast this with `execute_declare_transaction` in `transaction_impls.cairo` (lines 816–819), which enforces `prev_value=0` and `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`, and with `execute_entry_point` in `execute_entry_point.cairo` (lines 154–166), which uses `find_element` to look up the compiled class fact and will trap if the hash is absent. The `replace_class` path has no equivalent guard. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Once a contract's class hash is replaced with an undeclared value, every subsequent call to that contract will reach `execute_entry_point`, which calls `find_element` over `compiled_class_facts_bundle` searching for the bogus hash. `find_element` will not find it and the OS execution will fail (proof cannot be generated for that block, or the transaction will be permanently non-executable). Any ERC-20 tokens, ETH, or other assets stored in that contract's storage become permanently inaccessible — a **Critical: Permanent freezing of funds**. [4](#0-3) 

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly documented StarkNet syscall callable by any Cairo 1 contract. No privileged role is required. A malicious contract author can deploy a contract, call `replace_class` with an arbitrary felt (e.g., `0x1`), and the OS will commit the corrupted class hash to state. The attack is a single transaction and is irreversible once the block is proven and settled on L1. [5](#0-4) 

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, assert that it exists in the declared-class mapping. Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the existing guard in `execute_declare_transaction` and ensures only previously declared classes can be substituted. [6](#0-5) 

---

### Proof of Concept

1. Deploy a Cairo 1 contract `VictimWallet` that holds user funds (e.g., an ERC-20 balance in its storage).
2. From within `VictimWallet` (or a malicious contract that `VictimWallet` calls), invoke the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not present in `contract_class_changes`).
3. The OS executes `execute_replace_class`:
   - Gas is deducted successfully.
   - `class_hash = 0xdeadbeef` is read from the request.
   - No check against `contract_class_changes` is performed (the TODO at line 898 is skipped).
   - `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` for `VictimWallet`'s address.
4. The block is proven and settled. `VictimWallet`'s on-chain class hash is now `0xdeadbeef`.
5. Any future `call_contract` or `invoke` targeting `VictimWallet` reaches `execute_entry_point`, which calls `find_element` over `compiled_class_facts_bundle` for `0xdeadbeef`. The element is not found; the OS cannot produce a valid proof for any block containing such a call.
6. All funds in `VictimWallet`'s storage are permanently frozen. [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-177)
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-820)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );

```
