### Title
Missing Validation of Declared Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared class. A contract deployer can exploit this to permanently freeze funds stored in a contract by replacing its class hash with an undeclared value, rendering the contract permanently uncallable.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function updates a contract's class hash in `contract_state_changes` with the caller-supplied `request.class_hash` value, with no validation that this hash exists in the declared class set (`contract_class_changes`):

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
``` [1](#0-0) 

The function signature does not even include `contract_class_changes` as an implicit argument, making it structurally impossible to perform the missing check. The TODO comment at line 898 explicitly acknowledges this gap. [2](#0-1) 

The `contract_class_changes` dict (mapping `class_hash → compiled_class_hash`) is maintained separately from `contract_state_changes` (mapping `contract_address → StateEntry`). When the OS later attempts to execute a contract, it resolves the class hash from the contract's `StateEntry` and looks it up in the compiled class facts bundle. If the class hash is not present there, proof generation fails for any block containing a call to that contract. [3](#0-2) 

The `state_update` function in `state.cairo` squashes and commits the `contract_state_changes` dict to the Patricia tree without any cross-validation against `contract_class_changes`, so the invalid class hash is permanently committed to the global state root. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is committed, the contract's `StateEntry` in the global state permanently points to a non-existent class. Any subsequent transaction that attempts to call this contract causes the OS to fail proof generation for the block containing that call (the class cannot be resolved from the compiled class facts bundle). The sequencer is forced to exclude all such calls from future blocks. Any ERC-20 balances, ETH/STRK deposits, or other assets stored in the contract's storage are permanently inaccessible — the contract can never be called again.

---

### Likelihood Explanation

The `replace_class` syscall is available to any executing contract. A contract deployer — an unprivileged protocol participant — can:

1. Deploy a contract whose code calls `replace_class` with an arbitrary undeclared hash.
2. Attract user deposits (e.g., by advertising it as a vault or DeFi protocol).
3. Trigger the `replace_class` call (either directly or via a user-facing function).

No privileged role, leaked key, or operator cooperation is required. The entry path is fully reachable by an unprivileged contract deployer. [5](#0-4) 

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` and perform a `dict_read` on `contract_class_changes` with `key=class_hash` before applying the update. Assert that the returned value is non-zero (i.e., the class has been declared and has a valid compiled class hash). This mirrors the pattern already used in `execute_declare_transaction`, which enforces `prev_value=0` to prevent double-declaration. [6](#0-5) 

---

### Proof of Concept

1. **Attacker deploys** `MaliciousVault` (class hash `A`). The contract exposes a `deposit()` function and an `upgrade(new_hash)` function that calls `replace_class(new_hash)`.
2. **Users deposit** STRK into `MaliciousVault`. Balances are stored in the contract's storage under `contract_state_changes[vault_address]`.
3. **Attacker calls** `upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared on-chain. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No validation against `contract_class_changes` occurs (the function lacks the implicit argument).
   - `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` for `vault_address` into `contract_state_changes`.
4. **Block is committed.** `state_update` squashes and commits the state diff. The global state root now encodes `vault_address → class_hash=0xdeadbeef`. [7](#0-6) 
5. **Any future call** to `MaliciousVault` causes the OS to look up `0xdeadbeef` in the compiled class facts bundle. The lookup fails; the OS cannot generate a valid proof for any block containing such a call.
6. **All user funds** stored in `MaliciousVault`'s storage are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
