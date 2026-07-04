### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Contract Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract has been previously declared. Any contract can replace its own class with an arbitrary, undeclared class hash. The OS accepts this state transition unconditionally, permanently rendering the contract non-executable and freezing any funds held within it. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `execute_replace_class`, the OS reads `class_hash` from the syscall request and immediately writes it into `contract_state_changes` without consulting `contract_class_changes` (the dictionary that maps declared class hashes to their compiled class hashes):

```cairo
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
``` [1](#0-0) 

The `contract_class_changes` dictionary is the authoritative record of declared classes within the block. A check of the form `dict_read{dict_ptr=contract_class_changes}(key=class_hash)` followed by `assert_not_zero(compiled_class_hash)` is the required precondition — and it is entirely absent. The TODO comment on line 898 is the codebase's own acknowledgment of this gap. [2](#0-1) 

The `contract_class_changes` dictionary is initialized and populated in `initialize_state_changes` and updated during `execute_declare_transaction` (via `dict_update` with `prev_value=0`), making it the correct source of truth to validate against. [3](#0-2) [4](#0-3) 

**Vulnerability class mapping to the external report:** The external report describes a missing precondition check — no verification that funds were deposited before a close/transfer operation, allowing one entity's funds to be incorrectly consumed. Here, the missing precondition is: no verification that a class was declared before `replace_class` is allowed to commit it to state. In both cases, an operation that permanently alters ownership/accessibility of funds proceeds without confirming the required prior state exists.

---

### Impact Explanation

Once `replace_class` commits an undeclared class hash to `contract_state_changes`, the state root is updated to reflect a contract whose class hash has no corresponding compiled class anywhere in the system. Every subsequent call to that contract — including calls to withdraw, transfer, or recover funds — will fail at class resolution time. There is no recovery path: the OS has no mechanism to revert a committed state root, and the contract's storage (including token balances) is permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

- The `replace_class` syscall is available to every deployed contract with no privilege restriction.
- The OS imposes zero validation on the class hash argument.
- Realistic trigger paths include: (a) a developer mistake passing a wrong hash to `replace_class`; (b) an attacker who can invoke an upgrade entry point on a contract holding user funds (e.g., a vault, bridge, or DEX with an insufficiently guarded upgrade path); (c) a contract that computes its upgrade target dynamically from attacker-controlled calldata.
- The codebase's own TODO comment confirms the check was known to be missing and was deferred.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, add a read against `contract_class_changes` to confirm the class hash is declared:

```cairo
// Verify the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the existing pattern used in `execute_declare_transaction`, which enforces `prev_value=0` (i.e., the class must not already exist) before writing — the inverse guard is needed here. [5](#0-4) 

---

### Proof of Concept

1. Attacker deploys contract **C** (e.g., a simple vault) that accepts deposits from users. Users deposit STRK tokens; C's storage now records balances.
2. C exposes an `upgrade(new_class_hash: felt)` entry point (common pattern for upgradeable contracts).
3. Attacker calls `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is a felt that was never passed through `declare_transaction` and therefore does not exist in `contract_class_changes`.
4. The OS executes `execute_replace_class` for C. At line 896–914 of `syscall_impls.cairo`, it reads `class_hash = 0xdeadbeef` from the request and calls `dict_update` on `contract_state_changes`, setting C's class hash to `0xdeadbeef`. No check against `contract_class_changes` is performed.
5. The block is finalized. `state_update` squashes the changes and commits the new state root, which now records C's class hash as `0xdeadbeef`.
6. Any user who subsequently calls C to withdraw their funds triggers class resolution for `0xdeadbeef`. No compiled class exists. The call fails unconditionally.
7. All user funds in C are permanently frozen with no recovery mechanism at the protocol level. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L228-238)
```text
    // Update the state.
    %{ EnterScopeWithAliases %}
    let (squashed_os_state_update, state_update_output) = state_update{hash_ptr=pedersen_ptr}(
        os_state_update=OsStateUpdate(
            contract_state_changes_start=contract_state_changes_start,
            contract_state_changes_end=contract_state_changes,
            contract_class_changes_start=contract_class_changes_start,
            contract_class_changes_end=contract_class_changes,
        ),
        should_allocate_aliases=should_allocate_aliases(),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L261-276)
```text
// Initializes state changes dictionaries.
func initialize_state_changes() -> (
    contract_state_changes: DictAccess*, contract_class_changes: DictAccess*
) {
    %{ InitializeStateChanges %}
    // A dictionary from contract address to a dict of storage changes of type StateEntry.
    let (contract_state_changes: DictAccess*) = dict_new();

    %{ InitializeClassHashes %}
    // A dictionary from class hash to compiled class hash (Casm).
    let (contract_class_changes: DictAccess*) = dict_new();

    return (
        contract_state_changes=contract_state_changes, contract_class_changes=contract_class_changes
    );
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
