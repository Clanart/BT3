### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared contract class. This is an explicitly acknowledged gap (marked with a TODO). A contract can replace its own class hash with an arbitrary, undeclared value, after which the contract becomes permanently unexecutable and all funds held within it are irreversibly frozen.

### Finding Description
In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall as follows:

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

The TODO at line 898 explicitly acknowledges that the OS does not check whether the supplied `class_hash` has been declared. The `dict_update` unconditionally writes the caller-supplied hash into `contract_state_changes` with no Cairo constraint enforcing its validity. [2](#0-1) 

When a subsequent call is made to the affected contract, `execute_entry_point` performs:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — returns `0` (the default) for an undeclared class hash.
2. `find_element(..., key=compiled_class_hash)` — attempts to locate `compiled_class_hash=0` in `compiled_class_facts_bundle`. Since no compiled class with hash `0` exists, this call panics, making it impossible to generate a valid proof for any block containing a call to the frozen contract. [3](#0-2) 

The OS is the authoritative validator in StarkNet's trust model. Because the OS imposes no constraint on the replacement class hash, a sequencer can include a transaction where a contract calls `replace_class` with an undeclared hash, obtain a valid OS proof for the resulting block, and commit the corrupted state to L1.

### Impact Explanation
Once the state is committed on-chain with an undeclared class hash for a contract, that contract is permanently unexecutable. No entry point can be dispatched because `execute_entry_point` cannot resolve the compiled class. All ERC-20 tokens, NFTs, or other assets held in the contract's storage become permanently inaccessible. This matches the **Critical — Permanent freezing of funds** impact category.

### Likelihood Explanation
The missing check is explicitly documented with a TODO comment, confirming it is a known gap in the OS-level enforcement. Any contract can invoke the `replace_class` syscall. If the blockifier-level check is also absent (or bypassed by a sequencer that skips blockifier validation), an unprivileged user can trigger this directly. Even if the blockifier rejects such transactions, a sequencer can bypass blockifier validation and still produce a valid OS proof, since the OS imposes no constraint. The attack requires no special privilege beyond the ability to deploy and call a contract.

### Recommendation
Add a Cairo constraint in `execute_replace_class` that verifies the supplied `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry), mirroring the lookup performed in `execute_entry_point`. This resolves the acknowledged TODO and closes the gap between blockifier-level and OS-level enforcement:

```cairo
// Verify the class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

### Proof of Concept

1. **Attacker deploys** a contract `C` containing logic that calls `replace_class(undeclared_hash)` where `undeclared_hash` is any felt value that has never been passed to a `declare` transaction.
2. **Attacker sends** an invoke transaction calling `C.__execute__`, which internally calls the `replace_class` syscall with `undeclared_hash`.
3. **The OS** processes `execute_replace_class`: gas is deducted, `dict_update` writes `class_hash=undeclared_hash` into `contract_state_changes` for address `C`. No Cairo assertion checks whether `undeclared_hash` is declared. [4](#0-3) 
4. **State is committed**: `C`'s class hash in the Merkle state tree is now `undeclared_hash`.
5. **Any future call** to `C` reaches `execute_entry_point`, which calls `dict_read` on `contract_class_changes` for `undeclared_hash`, receives `0`, then calls `find_element` searching for compiled class hash `0`. This element does not exist in `compiled_class_facts_bundle`, causing the OS to be unable to generate a valid proof for any block containing a call to `C`. [3](#0-2) 
6. **Result**: All funds in `C` are permanently frozen. No valid proof can ever be generated for a block that attempts to execute `C`, making the funds irrecoverable.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
```
