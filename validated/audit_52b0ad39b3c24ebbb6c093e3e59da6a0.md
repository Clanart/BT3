### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` syscall handler (both the current and deprecated variants) updates a contract's class hash in the state without verifying that the supplied class hash corresponds to a previously declared class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared class hash, permanently corrupting its own state entry and freezing any funds held in its storage.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), the OS reads the caller's `state_entry`, constructs a `new_state_entry` with the attacker-supplied `class_hash`, and writes it unconditionally into `contract_state_changes` via `dict_update`. The only gas-related guard is whether there is enough gas to execute the syscall. There is **no check** that `class_hash` exists as a key in `contract_class_changes` (i.e., that it was ever declared).

The developers themselves acknowledge this gap with an explicit TODO:

```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [1](#0-0) 

The identical omission exists in the deprecated syscall path: [2](#0-1) 

By contrast, `execute_declare_transaction` correctly enforces `prev_value=0` to prevent overwriting an existing class, and `deploy_contract` asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing. The `replace_class` path has no equivalent guard on the *new* value. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

After `replace_class` succeeds with an undeclared hash `H`:

1. The contract's `StateEntry.class_hash` is set to `H` in `contract_state_changes`.
2. `H` has no entry in `contract_class_changes` (the class tree).
3. Every subsequent call to the contract causes the OS to look up class `H` for execution; since `H` is undeclared, execution cannot proceed.
4. All funds (tokens, ETH, ERC-20 balances) stored in the contract's storage are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.** [5](#0-4) 

---

### Likelihood Explanation

- `replace_class` is a standard, publicly available syscall reachable by any deployed contract without any privileged role.
- The attacker only needs to deploy (or control) a contract that calls `replace_class` with an arbitrary felt value.
- No leaked keys, operator collusion, or network-level attack is required.
- The missing check is explicitly flagged as a known TODO, confirming the gap is real and not an intentional design choice. [6](#0-5) 

---

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` (i.e., its `prev_value` is non-zero, meaning it was declared in a prior block or the current block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero before proceeding with `dict_update` on `contract_state_changes`. This mirrors the `prev_value=0` guard already used in `execute_declare_transaction`. [7](#0-6) 

---

### Proof of Concept

1. **Deploy** a contract `Victim` that holds user ERC-20 balances in its storage and exposes a public `freeze()` entry point.
2. Inside `freeze()`, emit the `replace_class` syscall with `class_hash = 0xdeadbeef` (any felt not present in the declared class tree).
3. **Call** `freeze()` via an `invoke` transaction. The OS routes the syscall to `execute_replace_class` in `syscall_impls.cairo`.
4. `execute_replace_class` reads `state_entry` for `Victim`, constructs `new_state_entry(class_hash=0xdeadbeef, ...)`, and calls `dict_update` — **no existence check is performed**.
5. The block is proven and the new state root commits `Victim.class_hash = 0xdeadbeef`.
6. Any subsequent `invoke` targeting `Victim` causes the OS to look up class `0xdeadbeef`; it is absent from the class tree, so execution fails unconditionally.
7. All token balances stored in `Victim`'s storage are permanently frozen. [8](#0-7)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
