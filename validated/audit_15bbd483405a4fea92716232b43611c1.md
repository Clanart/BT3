### Title
Unrestricted `replace_class` Accepts Undeclared Class Hash, Enabling Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the requested new class hash corresponds to a declared contract class. A malicious contract can invoke `replace_class` with an arbitrary undeclared hash, permanently rendering itself uncallable and freezing any funds it holds. The missing check is explicitly acknowledged in the code via a TODO comment.

---

### Finding Description

In `execute_replace_class` (lines 877–915 of `syscall_impls.cairo`), the OS reads the new `class_hash` from the syscall request and directly updates the contract's `StateEntry` in `contract_state_changes` without verifying that the hash corresponds to any declared class. The code itself contains an explicit TODO at line 898 acknowledging this gap:

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

The `class_hash` field comes directly from the user-supplied `ReplaceClassRequest` struct with no bounds or existence check. The `contract_class_changes` dict — which maps declared class hashes to their compiled class hashes — is never consulted. Any felt value, including `0x1` or a random hash that was never declared, is accepted and committed to state.

This is the direct analog of the commission-rate vulnerability: just as a validator could change their commission to any value at any time with no lockup, a contract can change its own class hash to any value at any time with no validation that the target class exists.

---

### Impact Explanation

Once a contract's class hash is set to an undeclared value:

1. **Every subsequent call to the contract fails.** The OS resolves the class to execute via the class hash stored in `StateEntry`. If that hash has no corresponding compiled class, execution cannot proceed.
2. **The contract cannot self-recover.** `replace_class` is a syscall that runs inside contract execution. If the contract cannot execute, it cannot call `replace_class` again.
3. **All funds held by the contract are permanently frozen.** There is no protocol-level escape hatch.

This satisfies the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack path is straightforward and requires no special protocol privileges:

1. A malicious actor deploys a contract that appears legitimate (e.g., a vault, lending pool, or bridge).
2. Users deposit funds into the contract.
3. The malicious actor calls a function in the contract that issues `replace_class(class_hash=0x1)` (or any

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
```text
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
```
