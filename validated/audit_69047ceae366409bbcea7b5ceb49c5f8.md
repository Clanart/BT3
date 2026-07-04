### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS program accepts any arbitrary `class_hash` value from the syscall request and writes it directly into `contract_state_changes` without verifying that the hash corresponds to a previously declared contract class. This is an exact analog of the "forbidden manager" irreversible-state-transition bug: once a contract's class hash is replaced with an undeclared value, the contract is permanently bricked and all funds locked in its storage are frozen with no recovery path.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `class_hash` directly from the syscall request and immediately applies it to the contract's state entry:

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
``` [1](#0-0) 

The developer-acknowledged TODO at line 898 confirms the missing check:

> `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [2](#0-1) 

The same omission exists in the deprecated path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;
    // No validation of class_hash against contract_class_changes
    ...
    dict_update{dict_ptr=contract_state_changes}(...);
``` [3](#0-2) 

By contrast, the `execute_declare_transaction` function correctly enforces `prev_value=0` to prevent re-declaration and validates the class hash pre-image before writing to `contract_class_changes`: [4](#0-3) 

The OS-level `contract_class_changes` dictionary is the authoritative registry of declared classes. A `replace_class` call with a hash absent from that registry produces a `StateEntry` whose `class_hash` has no corresponding compiled class, making every future entry-point dispatch against that contract fail permanently.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to an undeclared value, the OS has no mechanism to recover it. The contract's storage (which may hold token balances, vault assets, or any on-chain value) becomes permanently inaccessible. The state transition is committed to the global state root via `state_update` and `compute_class_commitment`, so the invalid class hash propagates into the Merkle tree and is indistinguishable from a valid state. [5](#0-4) 

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly documented StarkNet syscall reachable by any deployed contract. An attacker can:

1. Deploy a contract whose constructor or any external entry point calls `replace_class` with an arbitrary felt value (e.g., `1`).
2. Trigger that entry point from an unprivileged account transaction.
3. The OS will accept the syscall, write the undeclared hash, and commit it to state.

No privileged role, leaked key, or operator cooperation is required. The attacker controls the `class_hash` field in the syscall request entirely.

---

### Recommendation

Before writing the new `StateEntry`, assert that `class_hash` is present in `contract_class_changes` (i.e., its compiled class hash is non-zero). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero, mirroring the pattern used in `execute_declare_transaction`:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

Apply the same fix to `execute_replace_class` in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Deploy contract `A` with the following logic in its `__execute__` entry point:
   ```
   replace_class(class_hash=0x1)  // 0x1 is never declared
   ```
2. Submit an `invoke` transaction calling `A.__execute__`.
3. The OS dispatches `execute_replace_class` in `syscall_impls.cairo`.
4. Line 896 reads `class_hash = 0x1` from the request with no validation.
5. Lines 902–910 write `StateEntry(class_hash=0x1, ...)` into `contract_state_changes`.
6. The block is proven; the global state root now encodes `A.class_hash = 0x1`.
7. Any subsequent call to contract `A` fails at entry-point dispatch because no compiled class for `0x1` exists.
8. All funds in `A`'s storage are permanently frozen. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-328)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L76-87)
```text
    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );
```
