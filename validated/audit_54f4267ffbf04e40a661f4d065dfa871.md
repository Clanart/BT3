### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program accepts any arbitrary class hash in the `replace_class` syscall without verifying that a declared contract class exists at that hash. This is the direct StarkNet analog of the NuCypher Dispatcher vulnerability: just as the Dispatcher called `delegatecall` to an address without checking `extcodesize`, the OS commits a class-hash replacement to state without checking that the target class is declared. Any contract that calls `replace_class` with an undeclared class hash will have its class permanently set to an invalid value, making all future calls to it fail and permanently freezing any funds held by the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `REPLACE_CLASS` syscall. After deducting gas, it reads the caller's current `StateEntry` and unconditionally writes a new `StateEntry` with the caller-supplied `class_hash` into `contract_state_changes`: [1](#0-0) 

The developer-acknowledged TODO at line 898 explicitly states the missing check: [2](#0-1) 

No assertion, `dict_read` on `contract_class_changes`, or `find_element` lookup is performed to confirm that `request.class_hash` maps to a known compiled class before the state update is committed.

When a future call targets this contract, `execute_entry_point` performs:

1. `dict_read` on `contract_class_changes` keyed by the (now-undeclared) `class_hash` → returns `0` (uninitialized default).
2. `find_element` on `compiled_class_facts_bundle` keyed by `0` → fails if no compiled class with hash `0` exists. [3](#0-2) 

The contract becomes permanently unexecutable: every call to it will fail at the OS level, and any funds held by the contract are irrecoverably frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared class hash and the transaction is included in a block, the contract's `StateEntry.class_hash` is committed to the global state tree via `compute_contract_state_commitment`: [4](#0-3) 

The committed state is final. All subsequent calls to the bricked contract revert because the OS cannot resolve the class. ERC-20 balances, ETH, or any other assets stored in the contract's storage are permanently inaccessible — there is no recovery path.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on behalf of itself — no privileged role is required. An attacker can:

- Deploy a contract that accepts user deposits, then call `replace_class(undeclared_hash)` to freeze all deposited funds.
- Exploit a contract that exposes an unguarded `replace_class` call path.

The sequencer includes the `replace_class` transaction because the OS accepts it (no check), and the transaction itself does not revert. The missing check is confirmed by the in-code TODO, indicating this is a known gap, not a speculative one.

---

### Recommendation

Before committing the state update in `execute_replace_class`, verify that `request.class_hash` has a corresponding entry in `contract_class_changes` (i.e., it was previously declared). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the result is non-zero before writing the new `StateEntry`. This mirrors the check already performed in `execute_entry_point` during actual execution, and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

**Step 1 — Attacker deploys a fund-collecting contract** with a `brick_self` entry point:

```cairo
// Pseudocode for attacker's contract
@external
func brick_self() {
    // Pass any hash that has never been declared on-chain.
    replace_class(class_hash=0xdeadbeef_undeclared);
    return ();
}
```

**Step 2 — Users deposit funds** into the contract (e.g., via ERC-20 `transfer`).

**Step 3 — Attacker calls `brick_self()`.**

The OS processes `execute_replace_class`: [5](#0-4) 

No class-existence check is performed. `contract_state_changes` is updated with `class_hash = 0xdeadbeef_undeclared`. The transaction succeeds and is committed to state.

**Step 4 — Any future call to the contract** reaches `execute_entry_point`: [6](#0-5) 

`dict_read` on `contract_class_changes` returns `0` for the undeclared hash. `find_element` with key `0` fails (no compiled class with hash `0` exists). The call reverts. The contract's class hash in committed state remains `0xdeadbeef_undeclared`.

**Result:** All funds held by the contract are permanently frozen. No withdrawal, transfer, or administrative function can ever execute again.

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
