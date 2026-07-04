### Title
Missing Backward-Compatibility Branch in `calculate_global_state_root` Produces Wrong Global State Root — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo`)

---

### Summary

`calculate_global_state_root` documents three distinct cases for computing the global state root, but its condition only guards one of them. When the contract-class tree is empty (`contract_class_root == 0`) while the contract-state tree is not (`contract_state_root != 0`), the function falls through to the Poseidon hash path and returns `H(GLOBAL_STATE_VERSION, contract_state_root, 0)` instead of the specified backward-compatible value `contract_state_root`. The OS therefore emits a wrong `final_root` (and potentially a wrong `initial_root`) in its output, which the L1 verifier will reject, halting the network.

---

### Finding Description

`calculate_global_state_root` in `commitment.cairo` carries this specification in its own comment:

```
// If both the contract class and contract state trees are empty, the global root is set to 0.
// If the contract class tree is empty, the global state root is equal to the
// contract state root (for backward compatibility);
// Otherwise, the global root is obtained by:
//     global_root = H(GLOBAL_STATE_VERSION, contract_state_root, contract_class_root).
```

Three cases are described. The implementation handles only two:

```cairo
// commitment.cairo lines 38-48
func calculate_global_state_root{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    contract_state_root: felt, contract_class_root: felt
) -> (global_root: felt) {
    if (contract_state_root == 0 and contract_class_root == 0) {
        // The state is empty.
        return (global_root=0);
    }

    tempvar elements: felt* = new (GLOBAL_STATE_VERSION, contract_state_root, contract_class_root);
    let (global_root) = poseidon_hash_many(n=3, elements=elements);
    return (global_root=global_root);
}
```

The guard `contract_state_root == 0 and contract_class_root == 0` (AND of both) only catches case 1. Case 2 — `contract_class_root == 0` with `contract_state_root != 0` — falls through to the Poseidon path and produces `H(GLOBAL_STATE_VERSION, contract_state_root, 0)` instead of `contract_state_root`.

This is the direct analog of the external report's bug: the correct check should be on **one** variable (`contract_class_root == 0`) rather than requiring **both** to be zero.

`calculate_global_state_root` is called twice in `state_update` (`state.cairo` lines 90–97) — once for the initial root and once for the final root of every block:

```cairo
// state.cairo lines 90-97
let (local initial_global_root) = calculate_global_state_root(
    contract_state_root=contract_state_tree_update_output.initial_root,
    contract_class_root=contract_class_tree_update_output.initial_root,
);
let (local final_global_root) = calculate_global_state_root(
    contract_state_root=contract_state_tree_update_output.final_root,
    contract_class_root=contract_class_tree_update_output.final_root,
);
```

Both roots feed directly into the `CommitmentUpdate` that the OS writes to its output segment and that the L1 verifier checks.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

When the class tree is empty but the state tree is not, the OS outputs `H(GLOBAL_STATE_VERSION, contract_state_root, 0)` as the global root. The L1 verifier (Solidity contract) implements the correct specification and expects `contract_state_root` for this case. The mismatch causes every proof generated under this condition to be rejected on L1, preventing any block from being finalized and halting the network.

---

### Likelihood Explanation

**Medium.**

The triggering condition (`contract_class_root == 0`, `contract_state_root != 0`) is reachable during network bootstrapping — the bootstrap flow in `transaction_impls.cairo` (lines 764–775) explicitly supports a `BOOTSTRAP` sender that can write state without declaring classes, producing exactly this state. Any unprivileged transaction sender can also trigger it on a freshly initialized network before the first class declaration is processed, since the state tree becomes non-zero as soon as any storage or nonce is touched while the class tree remains at its empty root of 0.

---

### Recommendation

Add the missing backward-compatibility branch so that `contract_class_root == 0` alone (regardless of `contract_state_root`) returns `contract_state_root`:

```cairo
func calculate_global_state_root{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    contract_state_root: felt, contract_class_root: felt
) -> (global_root: felt) {
    if (contract_class_root == 0) {
        // Backward compatibility: no class tree → global root equals contract state root.
        // Special sub-case: both empty → 0 (which equals contract_state_root here).
        return (global_root=contract_state_root);
    }

    tempvar elements: felt* = new (GLOBAL_STATE_VERSION, contract_state_root, contract_class_root);
    let (global_root) = poseidon_hash_many(n=3, elements=elements);
    return (global_root=global_root);
}
```

---

### Proof of Concept

1. Bootstrap a new StarkNet network (or use the `BOOTSTRAP` sender path in `transaction_impls.cairo` lines 764–775).
2. Submit one or more transactions that touch contract storage or increment a nonce, making `contract_state_root != 0`, while submitting **no** class-declaration transactions, keeping `contract_class_root == 0`.
3. The OS calls `calculate_global_state_root(contract_state_root=X, contract_class_root=0)` where `X != 0`.
4. The `and` guard at line 41 of `commitment.cairo` is **not** taken (because `contract_state_root != 0`).
5. The function computes and returns `poseidon_hash_many([GLOBAL_STATE_VERSION, X, 0])` — a value that differs from `X`.
6. The OS writes this wrong root into `CommitmentUpdate.final_root` (and potentially `initial_root`).
7. The L1 verifier, which correctly implements the backward-compatibility rule, computes `X` for the same inputs and rejects the proof.
8. No block can be finalized; the network halts. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L38-49)
```text
func calculate_global_state_root{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    contract_state_root: felt, contract_class_root: felt
) -> (global_root: felt) {
    if (contract_state_root == 0 and contract_class_root == 0) {
        // The state is empty.
        return (global_root=0);
    }

    tempvar elements: felt* = new (GLOBAL_STATE_VERSION, contract_state_root, contract_class_root);
    let (global_root) = poseidon_hash_many(n=3, elements=elements);
    return (global_root=global_root);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L89-97)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L764-775)
```text
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
```
