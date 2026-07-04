### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash supplied by a contract corresponds to a previously declared class. A developer TODO comment in the code explicitly acknowledges this missing check. Because the OS blindly writes the caller-supplied hash into the contract state, any contract can set its own class hash to an arbitrary, undeclared value. Once that happens, no valid proof can ever be generated for a transaction that calls the bricked contract, permanently freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash corresponds to a declared class:

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

The same missing validation exists in the deprecated syscall path: [2](#0-1) 

When a future transaction calls the bricked contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // ← the invalid hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // ← 0 (uninitialized dict default)
);
``` [3](#0-2) 

`dict_read` returns `0` for an undeclared class hash. `find_element` then searches for a compiled class with hash `0`; if none exists, the hint-based search fails and no valid proof can be produced for any block containing a call to that contract.

The analog to the external report is direct: just as `InfraredVault.updateRewardsDuration()` exists but has no callable path from `Infrared`, the OS has a validation path that **should** exist inside `execute_replace_class` (confirmed by the TODO) but does not, making it impossible to ever recover a contract whose class hash has been set to an undeclared value.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:
1. The state transition is committed and cannot be rolled back.
2. Every subsequent attempt to call the contract causes `find_element` to fail inside the OS prover, making it impossible to generate a valid STARK proof for any block that includes such a call.
3. The sequencer is forced to permanently exclude the contract from all future blocks.
4. All ERC-20 tokens, ETH, or other assets held by the contract become permanently inaccessible.

---

### Likelihood Explanation

**Medium.**

- The `replace_class` syscall is a standard, publicly documented feature available to any Sierra contract.
- A malicious contract can accept user deposits and then call `replace_class` with an arbitrary felt value (e.g., `0xdeadbeef`) to freeze deposited funds.
- The missing check is explicitly acknowledged in a developer TODO dated 2026-01-01, confirming the team is aware the validation is absent.
- No privileged role or special access is required; any deployed contract can trigger this path.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). A minimal fix:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

The same fix must be applied to the deprecated path in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. **Deploy** a malicious contract (class `A`) that exposes a `drain_and_brick` entry point.
2. **Users deposit** tokens into the contract (e.g., via an ERC-20 `transfer`).
3. **Attacker sends** an invoke transaction calling `drain_and_brick`:
   - The contract calls `replace_class(0xdeadbeef)` — an arbitrary felt not corresponding to any declared class.
   - `execute_replace_class` writes `class_hash = 0xdeadbeef` into `contract_state_changes` with no validation.
   - The block is proven and accepted on L1.
4. **Any subsequent transaction** targeting the contract reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`.
   - `find_element(..., key=0)` → no compiled class found → proof generation fails.
5. **Result**: The sequencer can never include a valid call to the contract. All deposited funds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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
