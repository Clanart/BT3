### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash corresponds to a declared contract class before committing the state update. This allows any contract to replace its own class with an arbitrary, undeclared hash, permanently rendering the contract non-executable and freezing any funds it holds. The same flaw exists in the deprecated syscall path.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function (lines 877–916) updates a contract's class hash in `contract_state_changes` without verifying that the new class hash has ever been declared in `contract_class_changes`. The code contains an explicit, overdue TODO acknowledging this missing check:

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

The OS unconditionally writes the caller-supplied `class_hash` into the contract's state entry. There is no lookup into `contract_class_changes` to confirm the hash maps to a compiled class. The identical omission exists in the deprecated path: [2](#0-1) 

The `execute_declare_transaction` function, by contrast, correctly enforces `prev_value=0` to guarantee a class is declared before it can be used: [3](#0-2) 

`execute_replace_class` performs no equivalent guard.

---

### Impact Explanation

When a contract's class hash is set to an undeclared value, the sequencer cannot supply a valid compiled class for any subsequent call to that contract. The contract becomes permanently non-executable. All ERC-20 tokens, NFTs, or other assets held by the contract are irretrievably frozen. This matches the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack is reachable by any unprivileged transaction sender through two realistic paths:

1. **Proxy / upgradeable contract pattern** — A contract exposes an upgrade entry point that forwards a caller-supplied class hash directly to `replace_class`. An attacker calls this entry point with a hash that was never declared. The OS accepts it; the contract is frozen.

2. **Self-inflicted via a malicious contract** — A contract deployed by an attacker calls `replace_class(arbitrary_felt)` in its own logic. The OS accepts it without validation.

The TODO deadline of `1/1/2026` has already passed (today is 2026-07-03), confirming the check was intended but never implemented.

---

### Recommendation

Before committing the state update in `execute_replace_class`, verify that the new class hash exists in `contract_class_changes` (i.e., its compiled class hash is non-zero). Concretely:

```cairo
// Verify the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant already enforced by `execute_declare_transaction` via `prev_value=0` and `assert_not_zero(compiled_class_hash)`.

---

### Proof of Concept

1. Attacker deploys `ProxyContract` whose `upgrade(new_hash)` function calls `replace_class(new_hash)` with no local validation.
2. `ProxyContract` accumulates user funds (e.g., acts as a vault).
3. Attacker calls `ProxyContract.upgrade(0xdeadbeef)` where `0xdeadbeef` was never declared.
4. The OS executes `execute_replace_class`:
   - Reads `state_entry` for `ProxyContract`.
   - Writes `new StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
   - **No check against `contract_class_changes`.**
5. The block is proven and finalized with `ProxyContract.class_hash = 0xdeadbeef`.
6. Any subsequent call to `ProxyContract` requires the sequencer to supply a compiled class for `0xdeadbeef`; none exists. The contract is permanently non-executable.
7. All funds inside `ProxyContract` are permanently frozen.

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
