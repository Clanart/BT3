### Title
Missing Backward-Compatibility Branch in `calculate_global_state_root` Produces Incorrect State Root — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo`)

---

### Summary

`calculate_global_state_root` in `commitment.cairo` documents three distinct cases for computing the global state root, but only implements two of them. The missing branch — the backward-compatibility case where the contract class tree is empty (`contract_class_root == 0`) but the contract state tree is not — causes the OS to emit an incorrect global state root. Any block processed under this condition will produce a provably wrong commitment, which will be rejected by the L1 verifier, halting the network.

---

### Finding Description

The function `calculate_global_state_root` carries an explicit three-case specification in its own comment:

```
// If both the contract class and contract state trees are empty, the global root is set to 0.
// If the contract class tree is empty, the global state root is equal to the
// contract state root (for backward compatibility);
// Otherwise, the global root is obtained by:
//     global_root = H(GLOBAL_STATE_VERSION, contract_state_root, contract_class_root).
```

The implemented code is:

```cairo
func calculate_global_state_root{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    contract_state_root: felt, contract_class_root: felt
) -> (global_root: felt) {
    if (contract_state_root == 0 and contract_class_root == 0) {
        return (global_root=0);
    }

    tempvar elements: felt* = new (GLOBAL_STATE_VERSION, contract_state_root, contract_class_root);
    let (global_root) = poseidon_hash_many(n=3, elements=elements);
    return (global_root=global_root);
}
``` [1](#0-0) 

The code handles:
- **Case 1** (both zero) → returns `0` ✓
- **Case 3** (neither zero, or only state root zero) → returns poseidon hash ✓

But **Case 2** is entirely absent: when `contract_class_root == 0` and `contract_state_root != 0`, the code falls through to the `poseidon_hash_many` branch and returns `poseidon_hash(GLOBAL_STATE_VERSION, contract_state_root, 0)` instead of the specified `contract_state_root`.

This function is called twice per block inside `state_update` in `state.cairo` — once for the initial global root and once for the final global root: [2](#0-1) 

Both calls are affected. The corrupted `CommitmentUpdate` is then embedded in the `OsOutput` for the block: [3](#0-2) 

and serialized to the output segment via `process_os_output`, which is what the L1 verifier consumes. [4](#0-3) 

---

### Impact Explanation

The global state root is the single value committed to L1 to represent the entire StarkNet state. When the OS emits `poseidon_hash(GLOBAL_STATE_VERSION, contract_state_root, 0)` instead of `contract_state_root`, the proof submitted to L1 carries a root that does not match what the L1 verifier computes using the correct backward-compatibility rule. The L1 verifier will reject every such proof. No new blocks can be confirmed until the OS is corrected and redeployed.

**Allowed impact matched:** High — Network not being able to confirm new transactions (total network shutdown).

---

### Likelihood Explanation

The trigger condition is `contract_class_root == 0` with `contract_state_root != 0`. This occurs when the contract class Patricia tree has never received any entry — i.e., on a fresh network before any class declaration is processed, or on any network that migrates from the old single-tree state format (which is precisely the backward-compatibility scenario the comment describes). An unprivileged user submitting any state-changing transaction (invoke, deploy-account) to such a network, without any accompanying class declaration in the same block, will trigger the bug. No special role or key is required.

---

### Recommendation

Add the missing backward-compatibility branch before the general hash computation:

```cairo
func calculate_global_state_root{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    contract_state_root: felt, contract_class_root: felt
) -> (global_root: felt) {
    if (contract_state_root == 0 and contract_class_root == 0) {
        return (global_root=0);
    }

    // Backward compatibility: if the class tree is empty, the global root
    // equals the contract state root.
    if (contract_class_root == 0) {
        return (global_root=contract_state_root);
    }

    tempvar elements: felt* = new (GLOBAL_STATE_VERSION, contract_state_root, contract_class_root);
    let (global_root) = poseidon_hash_many(n=3, elements=elements);
    return (global_root=global_root);
}
```

---

### Proof of Concept

1. Deploy a fresh StarkNet network (class tree root = 0, state tree root = 0).
2. Submit a single `INVOKE_FUNCTION` transaction that writes to contract storage (no class declaration). After execution, `contract_state_root != 0`, `contract_class_root == 0`.
3. The OS calls `calculate_global_state_root(contract_state_root=X, contract_class_root=0)` where `X != 0`.
4. The `if (contract_state_root == 0 and contract_class_root == 0)` branch is **not** taken.
5. The code computes `poseidon_hash_many([GLOBAL_STATE_VERSION, X, 0])` and returns it as the global root.
6. The correct value per the specification is `X` (the contract state root).
7. The L1 verifier, implementing the correct rule, computes `X` and rejects the proof because the OS-produced root does not match.
8. No further blocks can be confirmed — total network shutdown.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L90-97)
```text
    let (local initial_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.initial_root,
        contract_class_root=contract_class_tree_update_output.initial_root,
    );
    let (local final_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.final_root,
        contract_class_root=contract_class_tree_update_output.final_root,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L247-252)
```text
    assert os_output_per_block_dst[0] = OsOutput(
        header=os_output_header,
        squashed_os_state_update=squashed_os_state_update,
        initial_carried_outputs=initial_carried_outputs,
        final_carried_outputs=final_carried_outputs,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L153-185)
```text
func process_os_output{
    output_ptr: felt*, range_check_ptr, ec_op_ptr: EcOpBuiltin*, poseidon_ptr: PoseidonBuiltin*
}(n_blocks: felt, os_outputs: OsOutput*, n_public_keys: felt, public_keys: felt*) {
    alloc_locals;
    // Guess whether to use KZG commitment scheme and whether to output the full state.
    // TODO(meshi): Once use_kzg_da field is used in the OS for the computation of fees and block
    //   hash, check that the `use_kzg_da` field is identical in all blocks in the multi-block.
    local use_kzg_da;
    local full_output;
    %{ WriteUseKzgDaAndFullOutputToMemory %}

    // Verify that the guessed values are 0 or 1.
    assert use_kzg_da * use_kzg_da = use_kzg_da;
    assert full_output * full_output = full_output;

    let final_os_output = combine_blocks(
        n=n_blocks,
        os_outputs=os_outputs,
        os_program_hash=0,
        use_kzg_da=use_kzg_da,
        full_output=full_output,
    );

    // Serialize OS output.
    %{ ConfigureKzgManager %}

    serialize_os_output(
        os_output=final_os_output,
        replace_keys_with_aliases=TRUE,
        n_public_keys=n_public_keys,
        public_keys=public_keys,
    );
    return ();
```
