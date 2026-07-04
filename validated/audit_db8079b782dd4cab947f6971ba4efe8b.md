### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking and Fund Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` syscall handler accepts any arbitrary `class_hash` value — including `0` (`UNINITIALIZED_CLASS_HASH`) and any undeclared hash — without verifying that the target class has been declared on-chain. This is explicitly acknowledged by a TODO comment in the production code. A contract (e.g., a bridge) that calls `replace_class` with an invalid or zero class hash will have its class permanently set to an unusable value, making all subsequent calls to it fail and permanently freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` in `syscall_impls.cairo`, the OS reads the requested `class_hash` from the syscall request and immediately writes it into the contract's `StateEntry` with no validation:

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

The TODO comment at line 898 is a developer-acknowledged gap: the OS was supposed to verify that `class_hash` exists in `contract_class_changes` (i.e., has been declared), but this check was never implemented. [2](#0-1) 

The same missing validation exists in the deprecated syscall path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;
    ...
    // No validation of class_hash whatsoever
    dict_update{dict_ptr=contract_state_changes}(...)
``` [3](#0-2) 

The sentinel value `UNINITIALIZED_CLASS_HASH = 0` is defined as the marker for a contract that has never been deployed: [4](#0-3) 

`deploy_contract` enforces that a contract address must have `class_hash == UNINITIALIZED_CLASS_HASH` before deployment, meaning `0` is the "no class" sentinel: [5](#0-4) 

If `replace_class(class_hash=0)` is accepted by the OS, the contract's `StateEntry.class_hash` becomes `0`, which is indistinguishable from an undeployed address. All subsequent entry point dispatches will fail to find any valid entry point, permanently bricking the contract.

By contrast, `declare` transactions correctly enforce `prev_value=0` to prevent overwriting and `assert_not_zero(compiled_class_hash)` to prevent declaring a zero hash: [6](#0-5) 

`replace_class` has no equivalent guard.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a bridge or token-holding contract calls `replace_class` with `class_hash = 0` or any undeclared hash (whether due to a contract-level access control bug, a malicious upgrade path, or a crafted L1→L2 message triggering an upgrade handler), the OS will accept the state transition. The contract's class hash is permanently set to an invalid value. Every subsequent call to that contract — including withdrawals, transfers, and redemptions — will fail at entry point dispatch. All funds held by the contract are permanently frozen with no recovery path, since the OS has no mechanism to revert a committed state root.

---

### Likelihood Explanation

**Medium.** The OS-level missing check is the necessary condition. The trigger requires a contract whose upgrade path can be reached by an unprivileged caller — for example, a bridge with a missing or bypassable access control guard on its `upgrade`/`replace_class` call, or a contract that accepts a class hash from L1 message payload without validation. Such patterns are common in bridge implementations. The explicit TODO comment confirms the developers are aware the check is absent and intended to add it.

---

### Recommendation

In `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add a validation step that asserts the requested `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry), mirroring the check that `declare` transactions enforce. Specifically:

- Perform a `dict_read` on `contract_class_changes` for the given `class_hash`.
- Assert the result is non-zero (i.e., the class has been declared).
- Reject the syscall with a failure response if the class is undeclared.

Additionally, add a check that `class_hash != UNINITIALIZED_CLASS_HASH` (i.e., `class_hash != 0`) before writing the new `StateEntry`.

---

### Proof of Concept

1. A bridge contract `B` holds user funds and exposes an `upgrade(new_class_hash)` function that calls `replace_class(new_class_hash)` with insufficient access control (or the attacker finds a way to supply the hash via an L1→L2 message payload).
2. Attacker submits a transaction calling `B.upgrade(class_hash=0)`.
3. The OS dispatches `execute_replace_class` with `class_hash = 0`.
4. Line 898 of `syscall_impls.cairo` has no validation — the TODO check was never implemented.
5. `dict_update` writes `StateEntry(class_hash=0, ...)` for contract `B` into `contract_state_changes`.
6. The state root is updated and committed on L1.
7. All subsequent calls to `B` (withdrawals, transfers) fail: the OS finds `class_hash = 0 = UNINITIALIZED_CLASS_HASH`, no entry points exist, every call reverts with `ENTRYPOINT_NOT_FOUND`.
8. All user funds in `B` are permanently frozen. No recovery is possible without a full protocol-level emergency migration.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
