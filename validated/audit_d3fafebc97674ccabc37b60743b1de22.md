### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary `class_hash` value from the caller without verifying that a class with that hash has actually been declared on-chain. This is an acknowledged missing check (marked with a `TODO` comment). A contract can irreversibly replace its own class hash with a non-existent value, permanently breaking the contract and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `ReplaceClassRequest` syscall:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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

The `class_hash` field is taken directly from `request.class_hash` — a caller-controlled value — and written into the contract's `StateEntry` in `contract_state_changes` with **no check** that the hash corresponds to a declared class. The `TODO` comment at line 898 explicitly acknowledges this missing validation. [2](#0-1) 

This state change is then committed permanently through `state_update` → `compute_contract_state_commitment` → Patricia tree update. Once committed, the invalid class hash becomes the canonical on-chain state for that contract address. [3](#0-2) 

When any future transaction attempts to invoke the contract, the OS reads the class hash from state and uses it to look up the compiled class. If the class hash is not declared, execution fails for every subsequent call — permanently.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract holds user funds (e.g., an ERC20 token, a vault, a multisig) and its class hash is replaced with an undeclared value, all entry points become unreachable. No transfer, withdrawal, or administrative function can ever be called again. The funds are permanently locked with no recovery path, because:

1. The invalid class hash is committed to the Patricia Merkle tree as the contract's canonical state.
2. There is no mechanism in the OS to "undo" a committed state change.
3. The contract cannot call `replace_class` again to fix itself, because calling any entry point requires a valid class hash to dispatch to.

---

### Likelihood Explanation

**Realistic.** The attack path requires only the ability to deploy a contract or interact with one that calls the `replace_class` syscall — capabilities available to any unprivileged user. Concrete scenarios:

- **Malicious deployer**: An attacker deploys a contract that solicits user deposits, then calls `replace_class` with a random non-existent felt value, permanently freezing all deposited funds.
- **Buggy contract**: A legitimate contract with an upgrade mechanism passes an incorrect class hash (e.g., a hash that was never declared, or a hash from a different chain). The OS accepts it without error, permanently breaking the contract.

The `TODO` comment confirms the development team is aware the check is missing and has not yet been implemented.

---

### Recommendation

Before committing the `replace_class` state change, the OS must verify that the supplied `class_hash` corresponds to a class that has been declared in `contract_class_changes` (or already exists in the class tree). Concretely, in `execute_replace_class`, after reading `request.class_hash`, perform a lookup in `contract_class_changes` to confirm the class hash maps to a non-zero compiled class hash. If the class is not found, write a failure response and return without updating state — analogous to how other syscalls handle invalid inputs.

---

### Proof of Concept

1. Attacker declares a valid class `C` and deploys contract `A` using class `C`. Contract `A` accepts ETH/token deposits and exposes an `upgrade(new_hash)` function that calls `replace_class(new_hash)`.
2. Users deposit funds into contract `A`.
3. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is an arbitrary undeclared felt.
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from `request.class_hash`.
   - Skips the missing declared-class check (line 898 TODO).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
5. `state_update` commits this to the Patricia tree. The block is proven and finalized on L1.
6. Any subsequent transaction targeting contract `A` reads `class_hash = 0xdeadbeef`, fails to find a compiled class, and reverts.
7. All user funds in contract `A` are permanently frozen with no recovery path. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L48-113)
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
