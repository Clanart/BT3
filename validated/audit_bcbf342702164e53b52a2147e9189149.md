### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied to `replace_class` refers to a previously declared contract class. The OS commits the unvalidated class hash into the global state root. Any contract whose class hash is replaced with an undeclared hash becomes permanently non-executable, freezing all funds it holds.

---

### Finding Description
In `syscall_impls.cairo`, `execute_replace_class` processes the `replace_class` syscall by directly writing the caller-supplied `class_hash` into `contract_state_changes` with no check that the hash exists in the set of declared classes:

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

The updated `contract_state_changes` dict flows directly into `state_update` → `compute_contract_state_commitment` → the global state root, which is serialized into the OS output and committed on-chain. Once committed, any future transaction targeting the contract will attempt to resolve the undeclared class hash from `compiled_class_facts`. Because the class was never declared, no compiled class fact exists for it; every subsequent call to the contract reverts at the OS level. The contract's storage and balance become permanently inaccessible.

The analog to the original report is exact: just as `set_rewards_period_duration()` performs a critical state mutation without emitting the corresponding event (making the change unverifiable), `execute_replace_class` performs a critical state mutation (class replacement) without verifying the corresponding protocol invariant (class must be declared), making the resulting state irrecoverable.

---

### Impact Explanation
**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other asset held in a contract whose class hash is replaced with an undeclared value is permanently locked. The state root is updated and proven; there is no rollback mechanism at the protocol level once the block is finalized on L1.

---

### Likelihood Explanation
The attack surface is reachable by any unprivileged transaction sender:

1. A contract that exposes a public or semi-public entry point calling `replace_class` with caller-influenced input (e.g., an upgradeable proxy whose upgrade function is insufficiently access-controlled) is vulnerable.
2. A developer who accidentally passes an undeclared hash (e.g., a hash computed off-chain before the corresponding `declare` transaction is included) triggers the same outcome.
3. The OS is the last line of defense for protocol invariants; the missing check means no on-chain enforcement exists.

---

### Recommendation
Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that `request.class_hash` is present in `contract_class_changes` (i.e., was declared in the current or a prior block). This mirrors the existing `prev_value=0` guard in `execute_declare_transaction` that prevents double-declaration. Concretely, perform a `dict_read` on `contract_class_changes` for `request.class_hash` and assert the returned compiled class hash is non-zero before proceeding.

---

### Proof of Concept

1. **Deploy** contract `Vault` holding user funds. `Vault` exposes `upgrade(new_class_hash)` which calls the `replace_class` syscall with the supplied argument. Access control is imperfect (e.g., guarded only by a storage flag an attacker can manipulate via a reentrancy or logic bug).

2. **Attacker** sends an `invoke` transaction calling `Vault.upgrade(0xdeadbeef)` where `0xdeadbeef` is a felt that has never been passed to a `declare` transaction.

3. **OS execution** reaches `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - The TODO-guarded check is absent; no `dict_read` on `contract_class_changes` is performed.
   - `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` for `Vault`'s address.

4. **`state_update`** squashes `contract_state_changes`, computes the new Patricia trie root including `Vault → 0xdeadbeef`, and returns `CommitmentUpdate(initial_root, final_root)`.

5. **`serialize_os_output`** writes `final_root` into the OS output header; the proof is generated and verified on L1. The state transition is finalized.

6. **Any subsequent call** to `Vault` causes the OS to look up `0xdeadbeef` in `compiled_class_facts`. No entry exists. The transaction reverts. All funds in `Vault` are permanently frozen with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L109-117)
```text
    serialize_output_header(os_output_header=os_output.header);

    let (local da_start, local da_end) = process_data_availability(
        state_updates_start=state_updates_start,
        state_updates_end=state_updates_ptr,
        compress_state_updates=compress_state_updates,
        n_keys=n_public_keys,
        public_keys=public_keys,
    );
```
