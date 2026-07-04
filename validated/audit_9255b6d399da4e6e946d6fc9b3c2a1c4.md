### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. An unprivileged contract deployer can exploit this to replace a contract's class hash with an arbitrary, undeclared value. Any subsequent call to that contract will fail at the OS proof level, permanently freezing all funds held by the contract.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS updates the contract's class hash in `contract_state_changes` without checking whether the new class hash exists in `contract_class_changes` (i.e., has been declared). The developers explicitly acknowledge this gap with a TODO comment:

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

The OS unconditionally writes the new (potentially undeclared) class hash into the contract state. No lookup against `contract_class_changes` is performed to confirm the class was ever declared.

When a subsequent transaction attempts to call the affected contract, `execute_entry_point` performs:

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

Because the class hash was never declared, `dict_read` returns `0` (`UNINITIALIZED_CLASS_HASH`). `find_element` then searches for a compiled class with hash `0`. Since no such class exists in the block's `compiled_class_facts_bundle`, `find_element` fails, making the proof invalid. The sequencer cannot include any call to this contract in a valid block — the contract is permanently uncallable.

The `UNINITIALIZED_CLASS_HASH = 0` sentinel is defined in `commitment.cairo`:

```cairo
const UNINITIALIZED_CLASS_HASH = 0;
``` [3](#0-2) 

The `replace_class` syscall is dispatched unconditionally to all contracts via `execute_syscalls`:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
``` [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is committed, the state change is irreversible. No subsequent block can include a valid call to that contract (the proof would be invalid). Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible. There is no recovery path: `replace_class` itself requires the contract to be callable, so the class cannot be corrected after the freeze.

---

### Likelihood Explanation

The `replace_class` syscall is available to every deployed contract without restriction. An attacker needs only to:

1. Deploy a contract (unprivileged, standard StarkNet operation) that exposes a function calling `replace_class` with an attacker-controlled hash.
2. Attract user deposits (e.g., by presenting the contract as a legitimate vault or DeFi protocol).
3. Call the freeze function with any hash value that has not been declared.

The OS performs no validation of the supplied class hash. The attack requires no privileged access, no leaked keys, and no operator cooperation. The only prerequisite is that the attacker controls a deployed contract — a standard capability for any network participant.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero (i.e., the class has been declared):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the invariant explicit and enforced at the point of replacement.

---

### Proof of Concept

1. **Deploy** `MaliciousVault` — a contract with a `deposit()` function and a `freeze(undeclared_hash: felt)` function that calls `replace_class(undeclared_hash)`.
2. **Attract deposits** — users call `deposit()`, transferring funds into `MaliciousVault`.
3. **Trigger freeze** — attacker calls `freeze(0xdeadbeef)` where `0xdeadbeef` is never declared.
4. **OS processes `replace_class`** — `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes` with no validation.
5. **Block committed** — the state now records `MaliciousVault.class_hash = 0xdeadbeef`.
6. **Subsequent call attempt** — any user calls `MaliciousVault`. `execute_entry_point` calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`. `find_element(..., key=0)` fails → proof invalid.
7. **Result** — the sequencer cannot include any call to `MaliciousVault` in a valid block. All deposited funds are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-197)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
```
