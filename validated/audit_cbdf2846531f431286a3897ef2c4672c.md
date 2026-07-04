### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying it corresponds to a previously declared contract class. A contract can therefore replace its own class hash with a non-existent hash. Any subsequent call to that contract in a future block will cause the OS proof to fail at `find_element` (which asserts the compiled class exists), making the contract permanently inaccessible and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class`, the new `class_hash` is read directly from the syscall request and written into `contract_state_changes` with no validation:

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

The TODO comment at line 898 explicitly acknowledges the missing check. No Cairo assertion verifies that `class_hash` is present in `contract_class_changes` (the declared-class dictionary).

When a future block includes a call to the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

`dict_read` on an undeclared key returns the default value `0`. `find_element` (unlike `search_sorted_optimistic`) **asserts** the element is found; if no compiled class fact with hash `0` exists, the proof fails unconditionally. Because `validate_compiled_class_facts_post_execution` independently verifies all guessed compiled class facts against their actual hashes, a prover cannot fabricate a fake entry for hash `0` either. [3](#0-2) 

The net result: the block containing the `replace_class` call proves successfully (no validation occurs at that point), the invalid class hash is committed to the global state root, and the contract is permanently bricked.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other asset held inside the affected contract becomes irrecoverable. The contract cannot be called, upgraded via a second `replace_class`, or otherwise recovered, because every future proof attempt that touches the contract aborts at `find_element`. The frozen state is committed on-chain and cannot be undone without a protocol-level upgrade.

---

### Likelihood Explanation

The attack surface is broad:

1. **Accidental**: A contract with a bug in its upgrade logic could call `replace_class` with a zero-padded or otherwise invalid hash. No off-chain tooling prevents this from reaching the OS.
2. **Intentional**: A malicious contract (e.g., a rug-pull DeFi vault) can be designed to call `replace_class(0xdeadbeef)` after collecting user deposits, permanently freezing all deposited funds.

The syscall is available to every Cairo 1 contract without any privilege requirement. The missing check is explicitly flagged in the source with a TODO dated 2026, confirming it is a known, unresolved gap in the production code.

---

### Recommendation

Before writing the new state entry, add a Cairo-level assertion that the requested class hash is present in `contract_class_changes`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the lookup already performed in `execute_entry_point` and ensures the constraint is enforced inside the proof, not merely in an off-chain hint.

---

### Proof of Concept

**Block N — `replace_class` with invalid hash:**

1. Attacker deploys a vault contract that accepts user deposits (holds ETH/ERC-20).
2. After collecting deposits, the vault contract executes the `replace_class` syscall with `class_hash = 0x1337` (not declared on-chain).
3. `execute_replace_class` writes `StateEntry(class_hash=0x1337, ...)` into `contract_state_changes` with no validation.
4. Block N is proven successfully; the global state root now encodes `class_hash=0x1337` for the vault address.

**Block N+1 — any call to the vault:**

5. A user (or the sequencer) includes a transaction calling the vault.
6. `execute_entry_point` calls `dict_read(contract_class_changes, key=0x1337)` → returns `0` (undeclared).
7. `find_element(..., key=0)` finds no matching compiled class fact → Cairo assertion fails.
8. Block N+1 cannot be proven; the sequencer must drop all transactions touching the vault.
9. The vault and all funds inside it are permanently inaccessible. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L142-177)
```text
func execute_entry_point{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
    alloc_locals;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L116-120)
```text
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```
