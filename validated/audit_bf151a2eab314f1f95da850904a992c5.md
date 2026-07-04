### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary caller-supplied class hash without verifying it corresponds to a declared contract class. This allows any contract to irreversibly set its own class hash to an undeclared value, permanently rendering the contract unexecutable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 878–916) reads the new class hash directly from the syscall request and writes it into the contract's `StateEntry` without any validation against the `contract_class_changes` dictionary. The codebase itself acknowledges this gap with an explicit TODO:

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
``` [1](#0-0) 

The `class_hash` field is taken verbatim from `request.class_hash` (line 896), which is fully attacker-controlled. No `dict_read` or `assert_not_zero` is performed to confirm the hash exists in `contract_class_changes`.

When the contract is subsequently invoked, `execute_entry_point` performs:

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

If `class_hash` is undeclared, `dict_read` returns `0` (the default for an uninitialized dict key, equal to `UNINITIALIZED_CLASS_HASH`). The subsequent `find_element` call then searches for a compiled class with hash `0`, which does not exist in the bundle. The prover cannot satisfy this constraint, making the contract permanently unexecutable. Every future call to the contract is reverted by the sequencer's off-chain execution, and the contract's state — including all held funds — is irrecoverable.

The `StateEntry` struct confirms that `class_hash` is a first-class field of committed on-chain state:

```cairo
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
``` [3](#0-2) 

Once committed to the Patricia Merkle Tree via `compute_contract_state_commitment`, the invalid class hash becomes part of the canonical state root and cannot be undone without a protocol-level intervention. [4](#0-3) 

---

### Impact Explanation

Any contract that calls `replace_class` with an undeclared class hash becomes permanently frozen. All ERC-20 tokens, ETH, STRK, or other assets held by the contract are irrecoverable. There is no protocol-level mechanism to restore a contract's class hash once it has been committed to the state tree with an invalid value. This satisfies the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack requires no special privileges. Any transaction sender can:
1. Deploy a contract whose logic calls the `replace_class` syscall with an arbitrary felt value.
2. Trigger that function after users have deposited funds.

The missing check is explicitly acknowledged in the production OS code via a dated TODO comment (`// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`), confirming the validation is absent in the currently deployed OS program. The attack path is direct and requires no complex setup beyond a standard contract deployment and invocation. [5](#0-4) 

---

### Recommendation

Inside `execute_replace_class`, before updating the contract's state entry, add a validation step that reads `contract_class_changes` with the provided `class_hash` as the key and asserts the returned compiled class hash is non-zero:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant enforced at declaration time in `execute_declare_transaction`, where `assert_not_zero(compiled_class_hash)` is called before writing to `contract_class_changes`. [6](#0-5) 

---

### Proof of Concept

1. Attacker deploys contract C (with a valid, declared class hash) that accepts user token deposits.
2. Users deposit funds into C; C's storage now holds user balances.
3. Attacker calls a function in C that issues the `replace_class` syscall with an arbitrary undeclared felt value (e.g., `0xdeadbeef`) as the new class hash.
4. The OS processes this without validation. `execute_replace_class` writes `class_hash=0xdeadbeef` into C's `StateEntry` and commits it to the state tree.
5. In any subsequent block, a call to C causes `execute_entry_point` to perform `dict_read(key=0xdeadbeef)` on `contract_class_changes`, receiving `0` (undeclared). `find_element` then searches for compiled class hash `0`, fails, and the call is reverted.
6. C is permanently unexecutable. All user funds are permanently frozen with no recovery path. [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
