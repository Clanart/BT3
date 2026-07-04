### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by a calling contract is a previously declared class. Any unprivileged contract can invoke `replace_class` with an arbitrary, undeclared class hash. Once the state is committed, the contract becomes permanently uncallable, freezing all funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash corresponds to a declared class:

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

The explicit TODO comment at line 898 confirms this check is intentionally absent from the current implementation.

After the block is committed, the contract's `StateEntry.class_hash` is set to the undeclared value. In any subsequent block, when `execute_entry_point` is called for this contract, the following sequence occurs:

**Step 1** — `dict_read` on `contract_class_changes` for the undeclared class hash returns `0` (the dictionary default for an unseen key):

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

**Step 2** — `find_element` is called with `key=0` (the returned compiled class hash). Since no compiled class fact has hash `0`, `find_element` panics, causing the entire OS execution to abort:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [3](#0-2) 

Because the OS would panic on any block that includes a call to the bricked contract, an honest sequencer cannot include such transactions. There is no recovery path: `replace_class` itself requires executing the contract (which is now impossible), and there is no privileged override in the OS.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

All assets (ERC-20 tokens, ETH, NFTs) held in a contract whose class hash has been replaced with an undeclared value are permanently inaccessible. No valid StarkNet OS proof can be generated for any block containing a call to that contract. The state is irrecoverable on-chain.

---

### Likelihood Explanation

**Medium.** The attack requires only that an unprivileged user:
1. Deploy (or control) a contract that calls the `replace_class` syscall.
2. Invoke that function with an arbitrary undeclared class hash.

Both steps are standard, permissionless user actions. No privileged role, leaked key, or external dependency is required. The `replace_class` syscall is a documented, gas-metered syscall available to any Cairo 1 contract.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the hash is present in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero:

```cairo
// Ensure the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

1. **Attacker deploys** `MaliciousVault`: a contract that accepts deposits and exposes a `freeze()` function that calls `replace_class(0xdeadbeef)`, where `0xdeadbeef` is never declared.
2. **Users deposit** funds into `MaliciousVault` (e.g., ERC-20 tokens).
3. **Attacker calls** `freeze()`. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is written to `contract_state_changes` with no validation.
   - The syscall succeeds; the block is proven and committed.
4. **In any subsequent block**, a user attempts to withdraw. The sequencer includes the withdrawal transaction. `execute_entry_point` is called:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`.
   - `find_element(..., key=0)` → **panics**; OS execution aborts.
5. The sequencer cannot include any transaction targeting `MaliciousVault`. All deposited funds are **permanently frozen**. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-156)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L161-166)
```text
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```
