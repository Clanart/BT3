### Title
Unvalidated Class Hash in `execute_replace_class` Enables Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash corresponds to a declared contract class. Any contract that exposes `replace_class` with user-controlled input can have its class replaced with an undeclared hash, permanently freezing the contract and all funds held within it. The missing check is explicitly acknowledged in the source with a TODO comment.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` processes the `replace_class` syscall. After reducing gas and reading the current state entry, it directly updates the contract's class hash to the caller-provided value with no validation that the new class hash exists in the declared class registry (`contract_class_changes`).

The critical section:

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

The TODO comment at line 898 is the root cause: the OS unconditionally accepts any felt value as a valid class hash. The updated `contract_state_changes` entry is then committed to the Patricia Merkle Tree via `compute_contract_state_commitment` in `commitment.cairo`, and the resulting global state root is serialized into the OS output and submitted to L1. [2](#0-1) 

The state change is permanent: it is squashed, hashed, and included in the `final_root` of the `CommitmentUpdate` struct that is written to the output segment. [3](#0-2) 

---

### Impact Explanation

Once a contract's class hash is set to an undeclared value and the block is proven and accepted on L1, the state is final. Any subsequent call to that contract will fail at the OS level because the compiled class for the new hash cannot be found in the compiled class facts bundle. No function of the contract can ever execute again. All ERC-20 balances, collateral, or other assets stored in that contract's storage slots become permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The `replace_class` syscall is a standard StarkNet syscall available to every contract. Upgradeable contracts routinely expose this path. If any such contract accepts a user-supplied class hash (e.g., through a governance vote, a permissioned upgrade function with a logic bug, or an intentionally open upgrade path), an unprivileged transaction sender can supply an undeclared hash. The OS-level missing validation means the attack succeeds regardless of the contract's intent, the resulting Cairo execution is valid, the proof is sound, and L1 accepts the state transition.

---

### Recommendation

Add a Cairo-level constraint inside `execute_replace_class` that verifies the provided `class_hash` exists in `contract_class_changes` (i.e., it was declared in the current block or a prior block). This must be a Cairo `assert` constraint, not merely a hint, so that the prover cannot bypass it. The check should mirror the `prev_value=0` enforcement already used in `execute_declare_transaction` to prevent re-declaration. [4](#0-3) 

---

### Proof of Concept

1. Deploy a contract `VulnerableUpgradeable` with a public function `upgrade(new_class_hash: felt252)` that internally calls the `replace_class` syscall with the caller-supplied `new_class_hash`.

2. As an unprivileged user, invoke `upgrade(0xdeadbeef)` where `0xdeadbeef` is a felt value that has never been declared via a `declare` transaction.

3. The sequencer includes the transaction. The OS executes `execute_replace_class` at `syscall_impls.cairo:878`. At line 898, the missing validation is skipped. The `dict_update` at line 906 writes `class_hash=0xdeadbeef` into `contract_state_changes`.

4. `state_update` in `state.cairo` squashes the dict, calls `compute_contract_state_commitment`, and produces a new `final_root` that encodes the poisoned class hash. This root is serialized into the OS output and verified on L1.

5. In any subsequent block, any transaction calling `VulnerableUpgradeable` causes the OS to look up compiled class facts for `0xdeadbeef`. No such entry exists. Execution fails unconditionally for every future call.

6. All funds stored in `VulnerableUpgradeable`'s storage are permanently frozen. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L76-111)
```text
func compute_contract_state_commitment{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_state_changes_start: DictAccess*,
    n_contract_state_changes: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> CommitmentUpdate {
    alloc_locals;

    // Hash the entries of the contract state changes to prepare the input for the commitment tree
    // multi-update.
    let (local hashed_state_changes: DictAccess*) = alloc();
    compute_contract_state_commitment_inner(
        state_changes=contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        hashed_state_changes=hashed_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the contracts' state tree.
    local initial_root;
    local final_root;

    %{ SetPreimageForStateCommitments %}

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_state_changes,
        n_updates=n_contract_state_changes,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (CommitmentUpdate(initial_root=initial_root, final_root=final_root));
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
