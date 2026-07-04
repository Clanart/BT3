### Title
`execute_replace_class` Accepts Undeclared Class Hash Without Validation, Enabling Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS program does not verify that the new class hash supplied by the caller has been declared in the `contract_class_changes` dictionary. A contract can replace its own class hash with any arbitrary, undeclared value. Once committed to state, no future transaction can successfully execute against that contract — because the OS will attempt to resolve the undeclared hash to a compiled class and fail — permanently freezing all funds held in the contract's storage.

---

### Finding Description

In `execute_replace_class`, the OS accepts the caller-supplied `request.class_hash` and writes it directly into `contract_state_changes` without any check that the hash corresponds to a previously declared class:

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

The developer-acknowledged TODO at line 898 confirms the missing guard. [1](#0-0) 

When any future transaction attempts to call the bricked contract, `execute_entry_point` reads the class hash from `contract_state_changes` and then looks up the corresponding compiled class hash from `contract_class_changes`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
```

For an undeclared class hash, `dict_read` returns the default value `0`. The OS then calls `find_element` to locate the compiled class with hash `0`:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

`find_element` is a hard assertion — if no compiled class with hash `0` exists, the OS execution aborts. The sequencer is therefore forced to permanently exclude any transaction targeting the bricked contract. [2](#0-1) 

The state commitment (`compute_contract_state_commitment`) faithfully records the undeclared class hash into the Patricia tree, making the bricked state permanent and provable. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that calls `replace_class` with an undeclared hash becomes permanently unexecutable. All ERC-20 balances, NFTs, or other assets stored in that contract's storage are irrecoverably frozen. There is no recovery path: restoring the class hash requires calling the contract, which is impossible once bricked.

This is directly analogous to the external report: just as a user under liquidation could deposit additional NFTs that were then swept — causing loss far beyond the intended scope — here a contract can perform a state-mutating operation (`replace_class`) that the OS accepts without checking a critical precondition (class declaration), resulting in a disproportionate and permanent loss.

---

### Likelihood Explanation

**Medium.** The `replace_class` syscall is available to any contract without privilege restrictions. An attacker who controls a shared contract (e.g., a multisig, a token contract, a DEX pool) can call it with an arbitrary hash in a single transaction. The attack requires no special role, no leaked key, and no external dependency. The only precondition is controlling a contract that holds other users' funds.

---

### Recommendation

Before updating `contract_state_changes`, verify that `request.class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared in the current or a prior block):

```cairo
// Add before dict_update:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This resolves the acknowledged TODO and closes the missing-state-check gap. [4](#0-3) 

---

### Proof of Concept

**Step 1 — Attacker deploys a shared vault contract** that holds user deposits (e.g., an ERC-20 balance in storage).

**Step 2 — Attacker submits an invoke transaction** that calls the `replace_class` syscall with an arbitrary felt value (e.g., `0xdeadbeef`) that has never been declared via a `declare` transaction.

**Step 3 — OS execution of `execute_replace_class`** accepts the call: gas is deducted, the revert log records the old class hash, and `contract_state_changes` is updated with the undeclared hash. No assertion fires. [5](#0-4) 

**Step 4 — State commitment** records the undeclared class hash into the Patricia tree via `hash_contract_state_changes` → `get_contract_state_hash`. The proof is valid and accepted on L1. [6](#0-5) 

**Step 5 — Any subsequent transaction** targeting the vault calls `execute_entry_point`. `dict_read` on `contract_class_changes` returns `0` for the undeclared hash. `find_element` with key `0` fails (hard assertion). The sequencer must permanently exclude all calls to this contract. [7](#0-6) 

**Result:** All user funds deposited in the vault are permanently frozen. The attacker paid only the gas cost of one invoke transaction.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-177)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L148-205)
```text
func hash_contract_state_changes{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_address: felt,
    prev_state: StateEntry*,
    new_state: StateEntry*,
    patricia_update_constants: PatriciaUpdateConstants*,
    hashed_state_changes: DictAccess*,
) {
    alloc_locals;

    local initial_contract_state_root;
    local final_contract_state_root;

    %{ SetPreimageForCurrentCommitmentInfo %}

    local state_dict_start: DictAccess* = prev_state.storage_ptr;
    local state_dict_end: DictAccess* = new_state.storage_ptr;
    local n_updates = (state_dict_end - state_dict_start) / DictAccess.SIZE;
    // Call patricia_update_using_update_constants() (or the read-optimized variant) instead of
    // patricia_update() in order not to repeat globals_pow2 calculation.
    local should_use_read_optimized: felt;
    %{ ShouldUseReadOptimizedPatriciaUpdate %}
    if (should_use_read_optimized != 0) {
        patricia_update_read_optimized(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    } else {
        patricia_update_using_update_constants(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    }
    local range_check_ptr = range_check_ptr;

    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
    assert hashed_state_changes.prev_value = prev_value;
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;

    return ();
```
