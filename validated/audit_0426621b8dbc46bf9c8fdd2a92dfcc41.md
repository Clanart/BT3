### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` accepts a caller-controlled `class_hash` from the syscall request and updates the contract's on-chain class hash **without validating** that the new hash corresponds to any declared contract class. A developer TODO comment explicitly acknowledges this missing check. An attacker can exploit this to permanently freeze funds held in any contract by replacing its class with an undeclared hash, rendering the contract permanently uncallable.

---

### Finding Description

The `execute_replace_class` function processes the `replace_class` syscall, which allows a contract to upgrade its own class. The function reads `class_hash` directly from the caller-supplied syscall request and writes it into `contract_state_changes` with no validation:

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

The TODO deadline was `1/1/2026`; today is `2026-07-03` — the check is overdue and still absent.

When a subsequent transaction calls the contract, `execute_entry_point` performs:

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

If the class hash written by `replace_class` is not declared, `dict_read` returns `0` (the default), and `find_element` cannot locate a compiled class for hash `0`. The block cannot be proven with any transaction targeting that contract. The contract is permanently inaccessible.

The `replace_class` syscall is reachable by any contract — it is dispatched unconditionally in `execute_syscalls`:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
``` [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 tokens, ETH, or other assets held in a contract whose class hash has been replaced with an undeclared value are permanently frozen. The contract can never be called again because the OS cannot resolve its class to a compiled program. No recovery path exists at the protocol level once the state is committed.

---

### Likelihood Explanation

The attack is directly reachable by any unprivileged transaction sender:

1. An attacker deploys a contract that accepts user deposits (e.g., a fake vault or DeFi protocol).
2. Users deposit funds.
3. The attacker sends a transaction that causes the contract to call `replace_class` with an arbitrary undeclared felt value (e.g., `0xdeadbeef`).
4. The OS writes the invalid class hash into `contract_state_changes` without any check.
5. The block is proven and the state is committed.
6. All future calls to the contract fail; funds are permanently frozen.

No privileged role, leaked key, or external dependency is required. The attacker only needs to control a deployed contract.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with the new `class_hash` as the key and assert the result is non-zero. This is exactly what the existing TODO comment calls for.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts user deposits and exposes a `freeze()` function that calls `replace_class(0xdeadbeef)`.
2. Users call `deposit()` and transfer tokens into `MaliciousVault`.
3. Attacker calls `freeze()`. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` (undeclared)
   - No validation is performed (TODO comment, line 898)
   - `contract_state_changes[MaliciousVault.address].class_hash = 0xdeadbeef`
4. Block is proven; state committed on-chain.
5. Any subsequent transaction targeting `MaliciousVault`:
   - `execute_entry_point` reads `class_hash = 0xdeadbeef`
   - `dict_read(contract_class_changes, 0xdeadbeef)` → `0`
   - `find_element(..., key=0)` → hint failure; block unprovable with this tx
   - Sequencer rejects all such transactions
6. `MaliciousVault` is permanently inaccessible. All deposited funds are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-197)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
```
