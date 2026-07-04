### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by the caller has been declared on-chain. A contract can therefore replace its own class with an arbitrary, non-existent hash. Once the block is proven and the state transition is committed, the contract becomes permanently uncallable, and any funds it holds are permanently frozen.

---

### Finding Description

`execute_replace_class` reads the new class hash directly from the syscall request and writes it into `contract_state_changes` without any check against the set of declared classes:

```cairo
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

The `// TODO` comment at line 898 is an explicit acknowledgement by the developers that this validation is absent. [1](#0-0) 

The revert log records the old class hash so the change can be undone if the *current* transaction reverts:

```cairo
assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
``` [2](#0-1) 

However, if the transaction **succeeds**, the new (undeclared) class hash is committed to the global state tree via `compute_contract_state_commitment`. [3](#0-2) 

After the block is proven and the state root is updated on L1, the contract's class hash permanently points to a non-existent class. Every subsequent call to the contract will fail to resolve the class, making the contract permanently uncallable and all funds it holds permanently frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds a balance of ETH, STRK, or any ERC-20 token (i.e., the token contract's storage maps `balances[contract_address]` to a non-zero amount) and whose class is replaced with an undeclared hash loses the ability to call `transfer` or any other entry point. Because the class cannot be resolved, every call reverts. The funds are irrecoverable: there is no upgrade path, no admin escape hatch, and no L1 mechanism to retrieve L2-locked tokens.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract from within a successful transaction. Realistic trigger paths include:

1. A contract with a bug in its upgrade logic passes the wrong hash (e.g., a hash that was never declared, or a hash of a class declared on a different chain).
2. A malicious contract intentionally calls `replace_class(arbitrary_felt)` to grief itself or to lock funds that were deposited into it by other users before the upgrade.
3. A reentrancy or cross-contract interaction causes an unexpected `replace_class` call with an attacker-controlled argument.

No privileged role is required; any deployed contract can issue this syscall.

---

### Recommendation

Before committing the class hash change, verify that the supplied `class_hash` exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for the new `class_hash` and assert the returned compiled class hash is non-zero before proceeding with the `dict_update`. This is exactly what the existing TODO comment calls for.

---

### Proof of Concept

1. Deploy contract `Victim` that holds 100 STRK (the STRK ERC-20 contract records `balances[Victim] = 100`).
2. Submit an `INVOKE` transaction that calls `Victim.__execute__`, which internally issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (never declared).
3. The OS executes `execute_replace_class`: gas is deducted, `contract_state_changes[Victim].class_hash` is set to `0xdeadbeef`, the revert log records the old hash, and the transaction completes successfully. [4](#0-3) 
4. `compute_contract_state_commitment` hashes the new `StateEntry` (with `class_hash = 0xdeadbeef`) into the Patricia tree and the new state root is published on L1. [5](#0-4) 
5. Any subsequent call to `Victim` (e.g., to transfer the 100 STRK) fails because class `0xdeadbeef` does not exist. The 100 STRK are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L148-206)
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
}
```
