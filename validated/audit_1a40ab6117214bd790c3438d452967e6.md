### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` directly writes an arbitrary caller-supplied class hash into the contract state without verifying that the hash corresponds to a declared contract class. This is the StarkNet OS analog of the "direct state setting without validation" pattern from the reference report. Any contract — deployed by an unprivileged user — can call `replace_class` with an undeclared class hash, permanently rendering itself non-executable and freezing all funds it holds.

---

### Finding Description

`execute_replace_class` (lines 878–916 of `syscall_impls.cairo`) processes the `replace_class` syscall by reading `request.class_hash` and immediately committing it to `contract_state_changes` with no check against `contract_class_changes`:

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
```

The team's own TODO comment at line 898 explicitly acknowledges the missing validation. The same pattern is present in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–328, which also performs no class-hash existence check.

The vulnerability class is **invalid state-transition acceptance**: the OS commits a state entry whose `class_hash` field points to a class that has never been declared, producing a permanently unexecutable contract.

This is directly analogous to the reference report's root cause: a value (`class_hash`) is **set directly** to an arbitrary caller-supplied value with no constraint that the new value is valid, just as `setAssetAllowances()` sets an allowance directly with no atomicity guarantee.

---

### Impact Explanation

Once a contract's `class_hash` is replaced with an undeclared hash, every subsequent call to that contract fails at class-lookup time — no entry point (including withdrawal functions) can execute. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

This affects not only the contract owner but every user who deposited assets into the contract (e.g., a vault, multisig, or DEX pool).

---

### Likelihood Explanation

The attack path requires only the ability to deploy a contract — an action available to any unprivileged user:

1. Attacker deploys a contract (or exploits an existing upgradeable contract's governance mechanism).
2. The contract calls `replace_class(undeclared_hash)`.
3. `execute_replace_class` accepts the call without validation and commits the invalid class hash to state.
4. The contract is permanently non-executable; all funds are frozen.

For contracts with multi-party upgrade mechanisms (DAOs, multisigs), a single malicious or compromised signer can trigger this path. The OS's missing validation is the necessary vulnerable step — if the OS rejected undeclared class hashes, the transaction would revert harmlessly.

---

### Recommendation

Before committing the new class hash, verify it exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the fix recommended in the reference report: constrain the new value before accepting it, rather than setting it directly without bounds.

---

### Proof of Concept

1. Unprivileged user deploys contract `C` with a valid class hash `A`.
2. `C.__execute__` calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is not present in `contract_class_changes`.
3. The OS executes `execute_replace_class` at `syscall_impls.cairo:878`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips the missing validation (acknowledged by the TODO at line 898).
   - Calls `dict_update` to write `StateEntry(class_hash=0xdeadbeef, ...)` for contract `C`.
4. The state transition is committed. Contract `C` now has `class_hash = 0xdeadbeef`.
5. Any subsequent `call_contract` or `invoke` targeting `C` fails: the OS looks up `0xdeadbeef` in `contract_class_changes`, finds nothing, and cannot dispatch any entry point.
6. All funds in `C`'s storage are permanently frozen. [1](#0-0) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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
