### Title
Missing Validation of Declared Class Hash in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary `class_hash` value without verifying that the class has been declared on-chain. This is a direct analog to the VaultBooster vulnerability where unsupported tokens are accepted without validation. A contract can replace its own class with an undeclared class hash, permanently rendering itself unexecutable and freezing all funds it holds.

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall by reading the requested `class_hash` from the syscall request and directly writing it into the contract's `StateEntry` in `contract_state_changes`, without any check that the class hash corresponds to a previously declared class:

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

The TODO comment at line 898 explicitly acknowledges this missing check. The OS developers themselves flagged this as a required validation that has not been implemented. [1](#0-0) 

After the state update, any subsequent transaction that calls the affected contract will cause the OS to look up the compiled class for the new (undeclared) class hash. Since no compiled class exists for that hash, the OS cannot execute the contract. The contract is permanently frozen.

### Impact Explanation

**Critical. Permanent freezing of funds.**

Any contract holding user funds (e.g., a token contract, a vault, a bridge escrow) that calls `replace_class` with an undeclared class hash — whether by mistake or by a malicious actor who controls the contract — will have its class hash set to an invalid value. All subsequent calls to that contract will fail at the OS level. There is no recovery path: the state has been committed with the invalid class hash, and no transaction can execute the contract to rescue the funds.

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly accessible syscall available to any Cairo contract. Any contract that calls it with an arbitrary felt value (e.g., a user-supplied class hash, a computed value, or a typo) will trigger this path. The missing validation is confirmed by the in-code TODO comment, meaning the OS currently ships without this guard. The attacker-controlled entry path is direct: deploy a contract, call `replace_class` with an undeclared hash, and the contract is permanently bricked. [2](#0-1) 

### Recommendation

Before updating `contract_state_changes` with the new class hash, verify that the class hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). This mirrors the check already enforced in `execute_declare_transaction` via `prev_value=0` in `dict_update`, which ensures a class can only be declared once and must exist before use. [3](#0-2) 

Concretely, add a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero before proceeding with the state update in `execute_replace_class`.

### Proof of Concept

1. Attacker deploys a contract `C` holding user funds (e.g., an ERC-20 balance).
2. `C` calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).
3. The OS executes `execute_replace_class`: no validation is performed; `contract_state_changes` is updated with `class_hash=0xdeadbeef` for contract `C`.
4. The block is proven and committed. The state now records `C.class_hash = 0xdeadbeef`.
5. Any subsequent transaction targeting `C` causes the OS to look up the compiled class for `0xdeadbeef`. No such class exists in `compiled_class_facts`.
6. The OS cannot execute `C`. All funds held by `C` are permanently frozen with no recovery mechanism. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-915)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
