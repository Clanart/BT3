### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract has ever been declared. A contract deployer can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts the state transition without validation, permanently rendering the contract unexecutable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function updates a contract's class hash in `contract_state_changes` without checking whether the new class hash exists in `contract_class_changes` (current block declarations) or the historical class commitment tree.

The code at lines 896–910:

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

The in-code TODO comment at line 898 explicitly acknowledges the missing check: *"Check that there is a declared contract class with the given hash."*

This is structurally identical to the H-01 root cause: in H-01, `update_token_balance` silently passes through the `None` (uninitialized) case, allowing a transfer to a chain where the token is not deployed. Here, `execute_replace_class` silently passes through an undeclared class hash, allowing a contract to adopt a class that does not exist in the class tree. In both cases, the OS/protocol accepts a state transition that references an uninitialized/non-existent entity, creating a permanent state mismatch.

After the block containing the `replace_class` call is committed:
- The contract's `class_hash` field in the state tree is set to the undeclared hash.
- No subsequent call to the contract can succeed: the OS cannot locate the compiled class for execution.
- The contract's storage and any token balances it holds are permanently inaccessible.
- The revert mechanism cannot help because the `replace_class` itself succeeded and was committed; future callers' transactions are reverted, but the contract's class hash remains corrupted. [1](#0-0) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds token balances (e.g., a vault, an ERC-20 contract, a multisig) can be permanently bricked. Once the undeclared class hash is committed to the state root, no valid OS proof can ever execute the contract again. The funds are irrecoverably frozen on-chain.

---

### Likelihood Explanation

The attack path is fully reachable by an unprivileged contract deployer:

1. The deployer publishes a contract that appears legitimate (e.g., a yield vault) and attracts user deposits.
2. The deployer calls a function on the contract that internally invokes `replace_class(undeclared_hash)`.
3. The OS processes the syscall, updates `contract_state_changes` with the undeclared hash, and commits the block — the proof is valid because the OS performs no class-existence check.
4. All subsequent calls to the contract fail; deposited funds are permanently frozen.

The `replace_class` syscall is a standard, permissionless syscall available to any executing contract. No privileged role is required beyond being the contract's own execution context. The TODO comment confirms the check has never been implemented. [2](#0-1) 

---

### Recommendation

Before updating `contract_state_changes`, verify that `class_hash` is a declared class. Concretely, the OS must confirm that `class_hash` appears either:

- In the current block's `contract_class_changes` dict (a class declared in this block), **or**
- In the historical class commitment tree (a class declared in a prior block).

Until the full historical lookup is implemented, a minimum guard is to assert that `class_hash != 0` (i.e., `class_hash != UNINITIALIZED_CLASS_HASH`) and that a corresponding entry exists in `contract_class_changes` for the current block. The longer-term fix requires a Merkle membership proof against the class tree root, analogous to how `deploy_contract` asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing. [3](#0-2) 

---

### Proof of Concept

```
// Attacker-controlled contract (simplified pseudocode)
@external
func drain_via_replace_class() {
    // 0xdeadbeef is never declared in any block
    replace_class(class_hash=0xdeadbeef);
    // Returns successfully; OS commits the state change.
}
```

**Step-by-step:**

1. Attacker deploys `MaliciousVault` — a contract that accepts ERC-20 deposits and exposes `drain_via_replace_class()`.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls `drain_via_replace_class()`.
4. Inside the OS, `execute_replace_class` is reached. `class_hash = 0xdeadbeef`. The TODO check is absent; `dict_update` writes the new `StateEntry` with `class_hash=0xdeadbeef` into `contract_state_changes`.
5. `state_update` in `state.cairo` squashes and commits the state diff. The block proof is valid.
6. In all subsequent blocks, any transaction targeting `MaliciousVault` causes the OS to look up compiled class `0xdeadbeef`, find nothing, and revert the transaction.
7. All deposited funds are permanently frozen. [1](#0-0) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L48-114)
```text
func state_update{poseidon_ptr: PoseidonBuiltin*, hash_ptr: HashBuiltin*, range_check_ptr}(
    os_state_update: OsStateUpdate, should_allocate_aliases: felt
) -> (squashed_os_state_update: SquashedOsStateUpdate*, state_update_output: CommitmentUpdate*) {
    alloc_locals;

    // Create PatriciaUpdateConstants struct for patricia update.
    let (local patricia_update_constants: PatriciaUpdateConstants*) = patricia_update_constants_new(
        );

    // (Maybe) allocate aliases and squash the final contract state tree.
    let (
        n_contract_state_changes, squashed_contract_state_changes_start
    ) = squash_state_changes_and_maybe_allocate_aliases(
        contract_state_changes_start=os_state_update.contract_state_changes_start,
        contract_state_changes_end=os_state_update.contract_state_changes_end,
        should_allocate_aliases=should_allocate_aliases,
    );

    // State is finalized.
    %{ ComputeCommitmentsOnFinalizedStateWithAliases %}

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
}
```
