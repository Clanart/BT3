### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract is actually declared (i.e., exists in `contract_class_changes`). This is an acknowledged missing check, noted by a TODO comment. Any contract can call `replace_class` with an arbitrary, undeclared class hash. The OS will accept and commit this state transition. Any subsequent call to that contract will fail permanently because the OS cannot resolve the undeclared class hash to a compiled class, permanently freezing any funds held by the contract.

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash from the syscall request and immediately updates `contract_state_changes` without validating that the class hash is declared:

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

The `contract_class_changes` dictionary maps class hashes to compiled class hashes. When a contract is subsequently called, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns `0` (the default for `dict_new()`). The OS then calls `find_element` searching for a `CompiledClassFact` with `hash=0`:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [3](#0-2) 

Since no compiled class fact with hash `0` exists, `find_element` will fail, making every future call to the contract permanently revert. The contract's state (including any token balances stored in its storage) is irrecoverably locked.

The `contract_state_changes` and `contract_class_changes` dictionaries are separate: `replace_class` only updates `contract_state_changes[contract_address].class_hash`, but the OS resolves execution through `contract_class_changes[class_hash]`. There is no cross-validation between the two at the point of the syscall. [4](#0-3) 

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract holding token balances (ERC-20, ERC-721, or native STRK/ETH via fee token transfers) that has its class replaced with an undeclared hash becomes permanently uncallable. All assets stored in that contract's storage are irrecoverably frozen. The state transition is committed to the proven block output and cannot be undone.

### Likelihood Explanation

**Medium.** The attack requires a contract to call `replace_class` with an undeclared hash. This is reachable via:

1. A malicious contract deployer who attracts user deposits and then calls `replace_class(undeclared_hash)` — a protocol-level rug pull variant.
2. Any upgradeable contract (proxy pattern) whose upgrade authorization logic has a flaw, allowing an attacker to supply an arbitrary class hash.

The `replace_class` syscall is a standard, publicly accessible syscall available to any deployed contract. No privileged OS role is required; the attacker only needs to be able to trigger the syscall from within a contract they control or exploit.

### Recommendation

Before updating `contract_state_changes`, verify that the requested class hash is present in `contract_class_changes` (i.e., has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the result is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` but makes it an explicit, enforced precondition of the `replace_class` syscall, consistent with the pattern used in `execute_declare_transaction` where `assert_not_zero(compiled_class_hash)` is enforced before updating `contract_class_changes`. [5](#0-4) 

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts token deposits from users and exposes a function `freeze()` that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls `freeze()`. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef`
   - No check against `contract_class_changes` is performed.
   - `contract_state_changes[MaliciousVault_address].class_hash = 0xdeadbeef` is committed.
4. Any subsequent call to `MaliciousVault` reaches `execute_entry_point`, which does `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`.
5. `find_element(..., key=0)` fails — the call reverts permanently.
6. All user deposits are permanently frozen with no recovery path. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
