### Title
Missing Zero-Value Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
`execute_replace_class` in the StarkNet OS Cairo program accepts any `class_hash` value — including `0` and undeclared hashes — without validation. This is the direct analog of the RocketPool missing `address(0x0)` check: just as `delegatecall` to `address(0x0)` silently succeeds without executing code, setting a contract's `class_hash` to `0` via `replace_class` causes all future OS-level executions of that contract to panic (because `find_element` cannot resolve hash `0`), permanently freezing any funds held by the contract.

### Finding Description
In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads `class_hash` directly from the user-supplied syscall request and writes it into the contract state without any validation:

```cairo
let class_hash = request.class_hash;
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
...
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
dict_update{dict_ptr=contract_state_changes}(key=contract_address, ...);
``` [1](#0-0) 

The TODO comment (overdue since 1 January 2026) explicitly acknowledges the missing check. No assertion of `class_hash != 0` or `class_hash is declared` exists anywhere in this function.

When a subsequent transaction calls the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // = 0
);
// compiled_class_hash is now 0 (default dict value)
let (compiled_class_fact: CompiledClassFact*) = find_element(
    ...
    key=compiled_class_hash,           // = 0
);
``` [2](#0-1) 

`find_element` panics if no compiled class with hash `0` exists in the facts bundle, making the contract permanently unexecutable at the OS level.

### Impact Explanation
Any contract that calls `replace_class(0)` (or `replace_class(<undeclared_hash>)`) has its `class_hash` permanently set to an unresolvable value in the committed state. The sequencer cannot include any future call to this contract in a provable block. All funds held by the contract — including funds deposited by third-party users — are permanently frozen with no recovery path. This matches the **Critical: Permanent freezing of funds** impact.

### Likelihood Explanation
The `replace_class` syscall is callable by any contract's own code. A malicious contract developer can:
1. Deploy a contract that appears legitimate (e.g., a vault or token).
2. Attract user deposits.
3. Trigger an internal `replace_class(0)` call (e.g., via a hidden admin function or a time-locked trigger).

The OS Cairo program is the authoritative execution layer; if it accepts `replace_class(0)`, the blockifier must also accept it to remain consistent. The overdue TODO confirms this is a known, unmitigated gap.

### Recommendation
Add an explicit non-zero and declared-class validation in `execute_replace_class`, analogous to how `deploy_contract.cairo` enforces `UNINITIALIZED_CLASS_HASH` checks:

```cairo
// After reading class_hash from request:
assert_not_zero(class_hash);
// Additionally verify class_hash exists in contract_class_changes (is declared).
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
``` [3](#0-2) 

### Proof of Concept
1. Deploy contract `Vault` that holds user ETH/STRK and exposes a `freeze()` function that calls the `replace_class` syscall with `class_hash=0`.
2. Users deposit funds into `Vault`.
3. Attacker calls `freeze()`. The OS executes `execute_replace_class` with `class_hash=0`, no validation fires, and `contract_state_changes` is updated: `Vault.class_hash = 0`.
4. State commitment is finalized on L1 with `Vault.class_hash = 0`.
5. Any subsequent invoke targeting `Vault` reaches `execute_entry_point` → `dict_read(key=0)` → `compiled_class_hash=0` → `find_element(key=0)` → OS panic → block unprovable → sequencer drops the call.
6. `Vault` is permanently unexecutable; all deposited funds are frozen with no recovery mechanism. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L53-54)
```text
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
