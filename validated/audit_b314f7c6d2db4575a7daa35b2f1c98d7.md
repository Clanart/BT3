### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Fund Freezing — (`File: execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash provided by a contract corresponds to a class that has actually been declared on the network. An explicit `TODO` comment in the code acknowledges this missing check. A contract can call `replace_class` with an arbitrary, undeclared class hash; the OS will silently accept it, commit the invalid state, and permanently brick the contract — freezing any funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash corresponds to a declared class:

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

The TODO comment at line 898 explicitly acknowledges the missing check. No assertion, `dict_read` on `contract_class_changes`, or `find_element` lookup is performed to confirm the new class hash is a known declared class.

Contrast this with `execute_entry_point.cairo`, which is the function that *uses* the class hash. It performs a `dict_read` on `contract_class_changes` and then a `find_element` lookup in the compiled class facts bundle:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

If the class hash stored in the contract state is undeclared, `dict_read` returns 0 (the default for uninitialized dict entries), and `find_element` with key `0` will fail with a Cairo assertion error during proof generation for any subsequent call to that contract. The contract becomes permanently uncallable.

The `StateEntry` struct stores the class hash as a plain `felt` with no invariant enforcing it must be declared: [3](#0-2) 

The state commitment machinery in `hash_contract_state_changes` and `compute_contract_state_commitment` will faithfully hash and commit this invalid class hash into the global state root, making the corruption permanent and provable on L1. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared hash and the block is proven and committed to L1:

1. The state root on L1 encodes the invalid class hash as the contract's class.
2. Every subsequent call to the contract (including withdrawal functions) will fail at the OS proof-generation level, because `find_element` cannot locate a compiled class for the undeclared hash.
3. All ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible — there is no recovery path, since the OS itself cannot produce a valid proof for any call to the contract.

This matches the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any deployed contract — it is an unprivileged operation. A contract author (or a contract with a bug) can invoke it with an arbitrary felt value as the class hash. The OS performs zero validation. The sequencer's off-chain blockifier may have a separate check, but the OS is the authoritative verifier for the proof system; if the OS does not enforce the invariant, a malicious or buggy sequencer can include such a transaction and produce a valid proof. The explicit `TODO` comment confirms the check is absent by design (deferred), not accidentally omitted.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the class hash is present in `contract_class_changes` (i.e., it has been declared). Concretely:

```cairo
// Verify the class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the OS the authoritative enforcer of the invariant, consistent with the principle that the OS must independently verify all state-transition correctness.

---

### Proof of Concept

**Actors:** Alice (attacker/contract owner), Bob (depositor).

**Setup:** Alice deploys a vault contract that accepts deposits from users. Bob deposits 1000 STRK into the vault.

**Attack:**

1. Alice calls a function in the vault contract that internally invokes the `replace_class` syscall with a class hash of `0xdeadbeef` — a hash that has never been declared on StarkNet.
2. The OS's `execute_replace_class` reads `class_hash = 0xdeadbeef` from the request, skips any existence check (per the TODO at line 898), and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
3. `compute_contract_state_commitment` hashes this entry into the state root; the block is proven and submitted to L1. The L1 verifier accepts the proof.
4. Bob attempts to withdraw his 1000 STRK. The sequencer tries to generate a proof for this call. `execute_entry_point` performs `dict_read` on `contract_class_changes` for key `0xdeadbeef`, gets `0` (undeclared), then calls `find_element` with key `0` — which fails because no compiled class with hash `0` exists in the bundle.
5. No valid proof can ever be generated for any call to the vault. Bob's 1000 STRK is permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
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
