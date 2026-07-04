### Title
Missing Class Declaration Validation in `execute_replace_class` Allows State Inconsistency Between Contract State Tree and Class Tree — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS updates `contract_state_changes` with a new `class_hash` but never verifies that this `class_hash` has been declared in `contract_class_changes`. This creates a persistent state inconsistency: the contract state tree records a class hash that does not exist in the class tree. Any contract can call `replace_class` with an arbitrary, undeclared class hash, permanently rendering itself unexecutable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (line 877) performs the following:

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

The function writes the new `class_hash` into `contract_state_changes` but makes **no corresponding write to `contract_class_changes`** and performs **no lookup to verify the class was ever declared**. The TODO comment at line 898 explicitly acknowledges this missing enforcement.

The same omission exists in the deprecated syscall path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    ...
    dict_update{dict_ptr=contract_state_changes}(...);
    // No check against contract_class_changes
}
``` [2](#0-1) 

**Analog to the original report**: In the MigrationAgent bug, `tokenSupply` was set once in the constructor but never updated when new tokens were minted, creating a divergence between `QiibeeToken` state and `MigrationAgent` state that broke `safetyInvariantCheck`. Here, `contract_state_changes` is updated with a new `class_hash` but `contract_class_changes` is never updated, creating a divergence between the contract state Merkle tree (which records the new class hash) and the class Merkle tree (which has no entry for it). The OS proof commits to both trees independently via `state_update`: [3](#0-2) 

The two commitments (`contract_state_tree_update_output` and `contract_class_tree_update_output`) are computed from separate dictionaries. After a `replace_class` with an undeclared hash, the contract state tree leaf points to a class hash that has no corresponding leaf in the class tree. The OS proof is still valid — no Cairo assertion fails — because the OS never cross-checks the two dictionaries.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` in `contract_state_changes` is set to an undeclared hash:
- The class does not exist in `compiled_class_facts` (the OS-level class registry).
- Every subsequent call to the contract fails at class lookup, with no recovery path.
- Any ERC-20 balances, NFTs, or other assets stored in the contract's storage are permanently inaccessible.
- The state transition is proven and accepted by L1, making the freeze irreversible at the protocol level.

---

### Likelihood Explanation

Any deployed contract can issue the `replace_class` syscall against itself. An attacker can:
1. Deploy a contract that accepts deposits (mimicking a vault, bridge, or staking contract).
2. Accumulate user funds.
3. Call `replace_class` with an arbitrary felt (e.g., `0xdeadbeef`) that was never declared.
4. The OS generates a valid proof; L1 accepts the state update; funds are frozen permanently.

The OS enforces no barrier — no `assert`, no dict lookup against `contract_class_changes`. The entry path is a standard user-submitted transaction. No privileged role is required.

---

### Recommendation

Inside `execute_replace_class` (both in `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add a verification step that the supplied `class_hash` exists as a key in `contract_class_changes` (i.e., was declared in the current or a prior block). This is exactly what the existing TODO comment calls for. Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned value is non-zero (i.e., a valid compiled class hash was registered).

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts ETH/ERC-20 deposits and exposes a `drain()` entry point callable only by the owner.
2. Users deposit funds; `MaliciousVault` accumulates balances in its storage.
3. Attacker calls `drain()`, which internally issues `replace_class(class_hash=0xdeadbeef)`.
4. The OS executes `execute_replace_class`:
   - `contract_state_changes[MaliciousVault.address].class_hash ← 0xdeadbeef`
   - `contract_class_changes` — **unchanged**
5. `state_update` commits both trees; the proof is valid; L1 accepts the block.
6. Any subsequent transaction targeting `MaliciousVault` fails: the OS cannot find `0xdeadbeef` in `compiled_class_facts`.
7. All deposited funds are permanently frozen with no withdrawal path. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L69-113)
```text
    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

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

    // Compute the initial and final roots of the global state.
    let (local initial_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.initial_root,
        contract_class_root=contract_class_tree_update_output.initial_root,
    );
    let (local final_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.final_root,
        contract_class_root=contract_class_tree_update_output.final_root,
    );

    // Prepare the return values.
    tempvar squashed_os_state_update = new SquashedOsStateUpdate(
        contract_state_changes=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        contract_class_changes=squashed_class_changes,
        n_class_updates=n_class_updates,
    );

    tempvar state_update_output = new CommitmentUpdate(
        initial_root=initial_global_root, final_root=final_global_root
    );

    return (
        squashed_os_state_update=squashed_os_state_update, state_update_output=state_update_output
    );
```
