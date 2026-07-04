### Title
`execute_replace_class` Accepts Arbitrary Undeclared Class Hash, Enabling Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program does not verify that the new class hash supplied by the caller is actually a declared class in the protocol state. This is an explicitly acknowledged missing guard (marked `TODO`). An unprivileged contract can call `replace_class` with an arbitrary or undeclared class hash — including `0` (UNINITIALIZED_CLASS_HASH) — causing the contract to become permanently non-executable and any funds it holds to be permanently frozen.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), after deducting gas, the OS reads the requested `class_hash` directly from the syscall request and writes it unconditionally into `contract_state_changes` for the calling contract address:

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
```

There is **no assertion** that `class_hash` exists in `contract_class_changes` (the declared class tree). The TODO comment at line 898 explicitly acknowledges this missing check. The same omission exists in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–329, which also performs no such validation.

By contrast, the `deploy_contract` flow (`deploy_contract.cairo`, line 53) enforces `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing, and the `execute_declare_transaction` flow (`transaction_impls.cairo`, line 816) enforces `assert_not_zero(compiled_class_hash)` and uses `prev_value=0` to prevent re-declaration. No equivalent guard exists for `replace_class`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract that calls `replace_class` with `class_hash = 0` (i.e., `UNINITIALIZED_CLASS_HASH`, defined in `state/commitment.cairo` line 16) will have its state entry's `class_hash` field set to `0`. From that point forward, any invocation of the contract will fail at the OS execution layer because the OS cannot resolve a compiled class for hash `0`. All assets (tokens, ETH, NFTs) held by the contract become permanently inaccessible. The state transition is committed to the Merkle tree and included in the block output, making it irreversible.

The same outcome occurs with any arbitrary non-zero felt that is not a declared class hash: the contract becomes permanently non-executable.

---

### Likelihood Explanation

The attack is reachable by any unprivileged user who can deploy a contract and submit a transaction. No privileged role, leaked key, or operator cooperation is required. The attacker:

1. Deploys any contract (or uses an existing one they control).
2. Submits a transaction invoking a function that calls the `replace_class` syscall with `class_hash = 0` or any undeclared hash.
3. The OS Cairo program accepts the syscall without validation and commits the corrupted state.

The sequencer may apply its own mempool-level filter, but the OS proof program — which is the authoritative protocol enforcer — does not enforce this invariant. Any sequencer (including a future one, or one with a bug in its filter) that includes such a transaction will produce a valid proof accepted by the L1 verifier.

---

### Recommendation

In `execute_replace_class` (`syscall_impls.cairo`), before writing the new `class_hash` into `contract_state_changes`, assert that the hash exists as a key in `contract_class_changes` with a non-zero value (i.e., it has been declared in the current or a prior block). This is exactly what the existing TODO comment describes. The same fix must be applied to the deprecated variant in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Declare and deploy a contract `Victim` that holds token balances and exposes a function `self_destruct()` which calls `replace_class(class_hash=0)`.
2. Submit a transaction calling `Victim.self_destruct()`.
3. The OS executes `execute_replace_class` with `class_hash = 0`. No validation is performed. The state entry for `Victim`'s address is updated to `class_hash = 0`.
4. The block is proven and verified on L1. The state root now encodes `Victim` with `class_hash = 0`.
5. Any subsequent call to `Victim` fails: the OS reads `class_hash = 0` from state, which is `UNINITIALIZED_CLASS_HASH`, and cannot dispatch execution. All funds in `Victim` are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
