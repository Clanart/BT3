### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. A contract can therefore replace its own class hash with an arbitrary undeclared value. Once committed, every subsequent call to that contract fails permanently, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any validation:

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

The embedded TODO comment explicitly acknowledges the missing check. The `class_hash` value is taken verbatim from `request.class_hash`, which is fully controlled by the calling contract.

When a future transaction attempts to call the affected contract, `execute_entry_point` performs:

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

If the stored class hash is undeclared, `dict_read` returns 0 (the default for an uninitialised dict entry), and `find_element` cannot locate a compiled class with hash 0. The prover cannot produce a valid witness for this lookup, so the sequencer's blockifier reverts every subsequent call to the contract. Because the `replace_class` state change was already committed in a prior block, the contract is permanently inaccessible.

---

### Impact Explanation

Any ERC-20 tokens, ETH, or other assets held by the affected contract are permanently frozen. There is no recovery path: the class hash stored in the Merkle state is invalid, every call reverts, and no upgrade or migration is possible. This satisfies the **Critical – Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack requires only the ability to deploy a contract and invoke a function — capabilities available to any unprivileged StarkNet user. A malicious actor can:

1. Deploy a contract (class A) that accepts user deposits and exposes a `freeze()` function that calls `replace_class` with an arbitrary undeclared hash.
2. Attract users to deposit funds.
3. Call `freeze()`.

No privileged access, leaked keys, or sequencer cooperation is required. The OS processes the `replace_class` call without complaint because the validation is absent. The attack is deterministic and irreversible once the block is finalised.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that `class_hash` is present in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled-class hash is non-zero. This is exactly what the existing TODO comment calls for and is consistent with how `execute_entry_point` later consumes the same mapping.

---

### Proof of Concept

1. **Declare** class A — a contract whose `freeze()` entry point executes the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value absent from the declared-class set).
2. **Deploy** an instance of class A at address `C`. Users deposit tokens into `C`.
3. **Invoke** `C.freeze()`. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`; no validation fires; `contract_state_changes[C].class_hash` is set to `0xdeadbeef` and committed to the Merkle tree.
4. **Attempt** any subsequent call to `C`. `execute_entry_point` reads `contract_class_changes[0xdeadbeef]` → 0, then calls `find_element(..., key=0)`. No compiled class with hash 0 exists; the blockifier reverts the transaction. This holds for every future transaction targeting `C`.
5. All funds deposited in `C` are permanently frozen with no recovery mechanism.

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
