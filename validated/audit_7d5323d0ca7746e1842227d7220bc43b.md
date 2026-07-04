### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS Cairo program accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared class. A contract can replace its own class hash with an undeclared or nonexistent value, permanently rendering itself non-executable and freezing any funds held in its storage.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the caller-supplied `class_hash` from the syscall request and writes it directly into the contract state without any validation:

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
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges the missing check. The OS program commits the new class hash into `contract_state_changes` and ultimately into the global state root without ever consulting `contract_class_changes` to confirm the hash was declared. This is the direct analog of the referenced report: just as an owner can remove themselves and break the system, a contract can replace its own class with an invalid hash and permanently break itself.

The class hash written here flows into `state_update` in `state/state.cairo`, which computes the final Merkle commitment: [2](#0-1) 

Once committed, the invalid class hash is part of the canonical state root. There is no recovery path.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class` is called with an undeclared hash:

1. The contract's `class_hash` field in the Patricia trie is set to an arbitrary felt that has no corresponding compiled class.
2. Every subsequent transaction targeting that contract address will fail at class resolution — no entry point can ever be executed again.
3. Any ERC-20 token balances (STRK, ETH, or other assets) stored in the contract's storage segment are permanently inaccessible. The storage still exists in the state, but there is no executable code to transfer or withdraw the assets.

This matches the allowed impact: **Critical. Permanent freezing of funds.**

---

### Likelihood Explanation

**Medium.**

- The `replace_class` syscall is a standard, publicly documented StarkNet syscall callable by any contract from any invoke transaction.
- No privileged role is required; any contract that calls `replace_class` with an arbitrary felt (e.g., `1`, `0xdeadbeef`, or any value not in `contract_class_changes`) triggers the bug.
- A malicious contract author can intentionally exploit this as a griefing vector: deploy a contract that accepts user deposits, then call `replace_class` with an invalid hash to permanently freeze deposited funds.
- The TODO comment confirms the check is known to be absent, meaning the gap is not accidental but deferred.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, validate that `request.class_hash` exists as a key in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `request.class_hash` and assert the returned compiled class hash is non-zero before proceeding with the state update. This mirrors the existing guard in `execute_declare_transaction` which uses `prev_value=0` to enforce that a class is declared exactly once. [3](#0-2) 

---

### Proof of Concept

1. Attacker deploys contract `C` (e.g., a token vault) that accepts deposits from users. Users deposit STRK tokens; the vault's storage now holds balances.
2. Attacker submits an invoke transaction calling `C.__execute__`, which internally issues a `replace_class` syscall with `class_hash = 0x1` (an undeclared felt).
3. The OS processes the syscall via `execute_replace_class`. No validation is performed against `contract_class_changes`. The state entry for `C` is updated: `class_hash = 0x1`.
4. `state_update` in `state/state.cairo` squashes and commits this change into the global state root.
5. All subsequent transactions targeting `C` fail at class resolution — the class hash `0x1` has no compiled class. The STRK balances stored in `C`'s storage are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L70-97)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
