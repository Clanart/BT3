### Title
Unvalidated Class Hash in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that a compiled class with that hash has been declared on-chain. A TODO comment in the code explicitly acknowledges this missing check. An unprivileged contract caller can exploit this to permanently invalidate a contract's class, making it uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `class_hash` directly from the syscall request and writes it into the contract state without any on-chain validation:

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

The OS never checks whether `class_hash` corresponds to a class that was declared via a `declare` transaction and is present in `compiled_class_facts`. The `validate_compiled_class_facts_post_execution` call at the end of `main` only validates classes *used during the current block's execution*, not classes written into state for future use. [2](#0-1) 

Once the contract's `class_hash` field in `contract_state_changes` is set to an undeclared hash, any future block that attempts to execute that contract will fail at the OS level: the OS cannot locate a compiled class for the hash, making proof generation impossible for any block containing a call to that contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After a successful `replace_class(undeclared_hash)` call is committed to state:

- The contract's `class_hash` in the global state tree is permanently set to an undeclared value.
- The sequencer cannot include any transaction that invokes the contract, because the OS would be unable to produce a valid proof (no compiled class exists for the hash).
- All assets (tokens, ETH, STRK) held in the contract's storage become permanently inaccessible — no withdrawal, transfer, or recovery function can be called.

This satisfies the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer:

1. An attacker deploys a contract containing a callable function that issues `replace_class(0xdeadbeef)` (any undeclared felt).
2. The attacker advertises the contract as a yield vault or token bridge and attracts user deposits.
3. The attacker calls the poisoning function; the OS accepts the syscall without validation.
4. The contract's class hash in state is now `0xdeadbeef`.
5. No future transaction calling the contract can be proven; all deposited funds are frozen.

Alternatively, any legitimate contract that exposes an upgrade path via `replace_class` (e.g., a proxy pattern) is vulnerable if an attacker can supply an arbitrary hash to that path — a common pattern in upgradeable contracts.

No privileged role, leaked key, or malicious sequencer is required. The attacker only needs to deploy a contract and submit ordinary transactions.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to state, assert that `class_hash` is present in the block's `contract_class_changes` dictionary (i.e., it was declared in this or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the enforcement already present for `declare` transactions, where `prev_value=0` and `assert_not_zero(compiled_class_hash)` are used. [3](#0-2) 

---

### Proof of Concept

**Step 1 — Attacker deploys a poisoning contract** (pseudocode):
```
@external
func poison_self() {
    replace_class(0x000000000000000000000000000000000000000000000000000000DEADBEEF);
}

@external
func deposit() { ... accept STRK ... }
```

**Step 2 — Users deposit funds** into the contract (believing it is legitimate).

**Step 3 — Attacker calls `poison_self()`.**

The OS executes `execute_replace_class`: [4](#0-3) 

`class_hash = 0xDEADBEEF` is written to `contract_state_changes` with no validation. The transaction succeeds and is committed to the state tree via `state_update`. [5](#0-4) 

**Step 4 — Any future transaction calling the contract** requires the OS to locate a compiled class for `0xDEADBEEF`. No such class exists in `compiled_class_facts`. The OS cannot generate a valid proof for any block containing such a call. The sequencer is forced to exclude all calls to the contract.

**Step 5 — All deposited funds are permanently frozen.** No withdrawal path exists.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
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
