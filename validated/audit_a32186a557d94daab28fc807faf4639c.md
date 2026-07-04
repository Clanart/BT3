### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract is actually a declared class. A contract can replace its own class hash with any arbitrary felt value, including one that has never been declared. Once the class hash is set to an undeclared value, the contract becomes permanently uncallable, freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. It reads the requested new `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any check that the hash corresponds to a declared class:

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

The `TODO` comment at line 898 explicitly acknowledges the missing check. The `class_hash` field of the new `StateEntry` is set to whatever value the contract provides, with no validation against `contract_class_changes` (the declared class registry).

Compare this to `execute_declare_transaction`, which enforces `prev_value=0` to prevent re-declaration and validates the class hash pre-image via `finalize_class_hash`. No equivalent guard exists in `execute_replace_class`. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

Once a contract's `class_hash` in `StateEntry` is set to an undeclared hash, the StarkNet OS cannot locate the corresponding compiled class (CASM) for any future call to that contract. Every subsequent transaction targeting the contract will fail at the OS execution layer. Because the contract itself is the only entity that could call `replace_class` to fix the situation, and it is now uncallable, the state is irreversible. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen.

This matches the **Critical: Permanent freezing of funds** impact category. [3](#0-2) 

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly accessible syscall available to any deployed contract. No privileged role is required. An attacker can:

1. Deploy a contract that accepts user funds (e.g., a fake vault or token).
2. After accumulating funds, call `replace_class` with an arbitrary undeclared felt value.
3. The OS processes the syscall without validation, committing the corrupted class hash to state.
4. The contract is permanently uncallable; all funds are frozen.

The attack requires only the ability to deploy a contract and submit a transaction — both unprivileged operations available to any network participant. [4](#0-3) 

---

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` exists in `contract_class_changes` (i.e., it was previously declared). The check should assert that a lookup of `class_hash` in the class changes dictionary returns a non-zero compiled class hash. This mirrors the enforcement already present in `execute_declare_transaction` via `prev_value=0` and `assert_not_zero(compiled_class_hash)`. [5](#0-4) 

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts token deposits.
2. Users deposit funds; the contract accumulates a balance.
3. Attacker submits a transaction that calls the `replace_class` syscall with `new_class_hash = 0xDEADBEEF` (an arbitrary undeclared felt).
4. `execute_replace_class` in the OS reads `request.class_hash = 0xDEADBEEF`, skips the missing declared-class check (TODO line 898), and calls `dict_update` to set `MaliciousVault`'s `StateEntry.class_hash = 0xDEADBEEF`.
5. The state transition is committed. The contract class tree now records `0xDEADBEEF` as the class hash for `MaliciousVault`.
6. Any subsequent call to `MaliciousVault` causes the OS to attempt to load the compiled class for `0xDEADBEEF`. No such class exists in `contract_class_changes`. Execution fails unconditionally.
7. All deposited funds are permanently frozen with no recovery path. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```
