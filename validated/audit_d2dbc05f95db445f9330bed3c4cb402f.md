### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared class. Any contract can replace its own class hash with an arbitrary felt value. If the value is not a valid declared class, the contract becomes permanently non-callable, freezing all funds it holds. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested new `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash corresponds to a declared class:

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
``` [1](#0-0) 

The `class_hash` field of the `ReplaceClassRequest` is attacker-controlled calldata. The OS performs no Cairo-level assertion that `class_hash` exists in `contract_class_changes` (the dict of declared/migrated classes) or in the pre-existing compiled class facts bundle. The TODO comment at line 898 explicitly acknowledges this missing invariant. [2](#0-1) 

By contrast, the `execute_declare_transaction` path enforces `prev_value=0` to prevent re-declaration, and the compiled class facts bundle is validated post-execution via `validate_compiled_class_facts_post_execution`. No equivalent guard exists for `replace_class`. [3](#0-2) 

---

### Impact Explanation

Once a contract's `class_hash` state entry is set to an undeclared felt value, every subsequent call to that contract will fail at class resolution time — the OS cannot find the bytecode to execute. The state change is committed to the Merkle tree and included in the proof output. There is no recovery path: the contract address is permanently bricked.

Any ERC-20 token balance, ETH, or other asset held in the contract's storage is permanently frozen. This matches the **Critical — Permanent freezing of funds** impact category. [4](#0-3) 

---

### Likelihood Explanation

The `replace_class` syscall is a standard StarkNet protocol feature used by upgradeable contracts. Many deployed contracts expose a public or semi-public upgrade entry point that internally calls `replace_class`. An attacker who can invoke such an entry point — either because it is permissionless, because they exploit a separate access-control bug, or because they are the contract owner acting maliciously — can supply an arbitrary felt as the new class hash. No special privilege beyond being a transaction sender is required to submit the syscall; the OS is the only enforcement layer, and it performs no validation.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, the OS must assert that the hash is present in either:

1. The `contract_class_changes` dict (a class declared in the current block), or
2. The pre-existing compiled class facts bundle (`os_global_context.compiled_class_facts_bundle`).

This is exactly the check the TODO comment describes. Until it is implemented, `replace_class` provides no safety guarantee against invalid class hashes. [5](#0-4) 

---

### Proof of Concept

1. Attacker deploys contract `C` (e.g., a wallet or token vault) holding user funds.
2. `C` exposes a public `upgrade(new_class_hash: felt)` function that calls the `replace_class` syscall with the provided argument.
3. Attacker submits an invoke transaction calling `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips the missing declared-class check (line 898 TODO).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` for address `C`.
5. The block is proven and the state root is updated with `C`'s new class hash = `0xdeadbeef`.
6. Any subsequent call to `C` fails: the OS cannot resolve `0xdeadbeef` to any compiled class.
7. All funds stored in `C`'s storage slots are permanently inaccessible. [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
