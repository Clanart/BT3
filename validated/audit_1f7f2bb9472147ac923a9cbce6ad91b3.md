### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Fund Freeze — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash has been declared on-chain before updating a contract's class. A contract can therefore replace its own class hash with an arbitrary, undeclared value. Once this happens, every subsequent transaction targeting that contract will be permanently reverted by the sequencer (because executing it would cause the OS proof to fail), freezing any funds held by the contract forever.

---

### Finding Description

`execute_replace_class` updates `contract_state_changes` with the caller-supplied `class_hash` without checking whether that hash exists in `contract_class_changes` (the declared-class registry):

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

The developers themselves flagged this gap with the `TODO` comment at line 898. [1](#0-0) 

When any future transaction calls the affected contract, `execute_entry_point` reads the (now undeclared) class hash and looks up its compiled class hash:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared → returns 0
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // key = 0, not present → proof failure
);
``` [2](#0-1) 

`find_element` asserts the element exists; if `compiled_class_hash` is 0 (the default for an undeclared class), the assertion fails and the entire OS proof is invalid. To avoid producing an invalid proof, the sequencer is forced to mark every call to the affected contract as reverted before execution reaches `execute_entry_point`. The reverted path in `execute_invoke_function_transaction` skips execution entirely:

```cairo
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    ...
    non_reverting_select_execute_entry_point_func(...);
}
``` [3](#0-2) 

Because `replace_class` can only be called from within the contract's own execution, and every execution of the contract is now reverted, there is no on-chain path to restore the class hash. The contract becomes permanently inert.

---

### Impact Explanation

Any funds (ERC-20 tokens, ETH bridged via L1→L2, or any other assets) held in the storage of the affected contract are permanently inaccessible. No withdrawal, transfer, or administrative function can execute because every transaction targeting the contract is unconditionally reverted by the sequencer. This satisfies the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack path requires only that a contract calls `replace_class` with an undeclared class hash. This is an unprivileged, single-transaction operation available to any deployed contract. Realistic scenarios include:

- A malicious contract that lures users to deposit funds and then calls `replace_class(arbitrary_undeclared_hash)` as a rug-pull mechanism.
- A legitimate contract with a logic bug or missing access control on an upgrade function that an attacker exploits to supply an undeclared hash.
- A contract that intentionally or accidentally passes a typo'd / wrong hash to `replace_class`.

No privileged role, leaked key, or external dependency is required.

---

### Recommendation

Inside `execute_replace_class`, before updating `contract_state_changes`, assert that the requested class hash is present in `contract_class_changes` (i.e., has been declared). The developers already identified this gap:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The fix is to perform a `dict_read` on `contract_class_changes` for the new `class_hash` and assert the returned `compiled_class_hash` is non-zero before proceeding with the state update. This mirrors how `execute_entry_point` itself relies on the class being declared. [4](#0-3) 

---

### Proof of Concept

1. **Setup**: Deploy contract `Vault` (class hash `C1`, declared) that holds user ERC-20 deposits and exposes a `replace_class` wrapper callable by its owner.
2. **Phase 1 — Funds deposited**: Users call `Vault.deposit()`. Funds accumulate in `Vault`'s storage. State: `contract_state_changes[Vault].class_hash = C1`.
3. **Phase 2 — Malicious upgrade**: The owner (or an attacker exploiting a missing access-control check) calls `Vault.upgrade(C_FAKE)` where `C_FAKE` is never declared. Internally this issues `replace_class(C_FAKE)`. The OS accepts this because `execute_replace_class` performs no declared-class check. State: `contract_state_changes[Vault].class_hash = C_FAKE`.
4. **Permanent freeze**: Any user submits `Vault.withdraw()`. The sequencer resolves `class_hash = C_FAKE`, finds `contract_class_changes[C_FAKE] = 0`, and knows that executing the transaction would cause `find_element(key=0)` to fail the proof. The sequencer marks the transaction `is_reverted = TRUE`. Execution is skipped; no state change occurs. The funds remain in `Vault`'s storage with no callable entry point. Every future attempt repeats step 4 identically — the freeze is permanent.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L341-358)
```text
    if (is_reverted == FALSE) {
        // Execute only non-reverted transactions.
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
    } else {
        // Align the stack with the `if` branch to avoid revoked references.
        tempvar range_check_ptr = range_check_ptr;
        tempvar remaining_gas = remaining_gas;
        tempvar builtin_ptrs = builtin_ptrs;
        tempvar contract_state_changes = contract_state_changes;
        tempvar contract_class_changes = contract_class_changes;
        tempvar outputs = outputs;
        tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
    }
```
