### Title
Missing Validation of `class_hash` in `execute_replace_class` Allows Permanent Freezal of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the supplied `class_hash` corresponds to a previously declared contract class. Any contract can call `replace_class` with an arbitrary, undeclared class hash, permanently bricking itself and freezing any funds it holds.

### Finding Description
In `execute_replace_class`, the new `class_hash` from the request is written directly into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (the declared-class registry). The code even contains an explicit TODO acknowledging this missing check:

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

When any subsequent call is made to the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns `0`, and `find_element` fails to locate a matching `CompiledClassFact`, causing every future call to the contract to abort. The contract becomes permanently unexecutable.

### Impact Explanation
**Critical — Permanent freezing of funds.**

Any contract holding ERC-20 tokens, ETH, STRK, or other assets that calls `replace_class` with an undeclared hash loses the ability to execute any entry point forever. Withdrawals, transfers, and all other operations become impossible. The funds are irrecoverably locked on-chain.

### Likelihood Explanation
The `replace_class` syscall is a standard, documented StarkNet syscall callable by any contract. A malicious or buggy contract can trivially supply an arbitrary felt value as `class_hash`. No privileged role, leaked key, or external dependency is required. The attack surface is every deployed contract that uses `replace_class`.

### Recommendation
Before writing the new `class_hash` into `contract_state_changes`, assert that the hash is present in `contract_class_changes` (i.e., it has been declared). This mirrors the pattern already used in `execute_declare_transaction`, which enforces `prev_value=0` to guarantee a class is declared exactly once:

```cairo
// Validate the new class_hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
``` [3](#0-2) 

### Proof of Concept

1. Deploy a contract `Victim` that holds user funds and implements `replace_class`.
2. From within `Victim` (or via a call), invoke the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not present in `contract_class_changes`).
3. The OS processes the syscall through `execute_replace_class`, which writes `class_hash=0xdeadbeef` into `contract_state_changes` for `Victim`'s address with no validation.
4. Any subsequent transaction targeting `Victim` reaches `execute_entry_point`, which calls `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` → returns `0`.
5. `find_element` fails to locate a `CompiledClassFact` with hash `0`, aborting execution.
6. All entry points of `Victim` are permanently unreachable; all funds are frozen. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
