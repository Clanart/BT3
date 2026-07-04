### Title
Unvalidated `class_hash` in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not validate that the caller-supplied `class_hash` corresponds to a previously declared contract class. An attacker who controls a contract (e.g., a DeFi vault that holds user deposits) can invoke `replace_class` with an arbitrary, undeclared class hash. The OS commits this invalid hash to state without any check. All subsequent calls to that contract will fail because the OS cannot resolve the class hash to a compiled class, permanently freezing any funds held in the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

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
```

The developer-acknowledged TODO comment confirms the missing check: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [1](#0-0) 

The `class_hash` value from `request.class_hash` is written directly into `contract_state_changes` with no membership check against `contract_class_changes` (the dict of declared Sierra→compiled class mappings). [2](#0-1) 

When any subsequent call targets that contract, `execute_entry_point` performs:

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
``` [3](#0-2) 

Because the arbitrary hash was never declared, `dict_read` returns 0 (the default uninitialized value), and `find_element` with key `0` fails to locate a compiled class fact, causing an OS-level panic. The contract becomes permanently unexecutable, and all funds stored in its storage are irrecoverably frozen.

This is the direct analog of the LendingPool `redeem` bug: just as `redeem` trusted a caller-supplied `_aToken` address without validating it against the reserve registry, `execute_replace_class` trusts a caller-supplied `class_hash` without validating it against the declared class registry.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that (a) holds user funds in its storage and (b) is controlled by a malicious operator can have its class replaced with an arbitrary undeclared hash. Once committed to state, the contract is permanently unexecutable. No withdrawal, transfer, or recovery function can be called. All funds locked in the contract's storage are frozen forever with no protocol-level recovery path.

---

### Likelihood Explanation

The `replace_class` syscall is reachable by any deployed contract without any privileged role. An attacker needs only to:
1. Deploy a contract (or be the operator of an existing one).
2. Attract user deposits (standard DeFi pattern).
3. Issue a single transaction calling `replace_class` with an arbitrary felt value.

The missing check is explicitly acknowledged in the codebase via a TODO comment, confirming it is a known gap in the current implementation. The attack requires no leaked keys, no 51% attack, and no external dependency — only a standard user transaction.

---

### Recommendation

In `execute_replace_class`, before writing the new `class_hash` to `contract_state_changes`, verify that the hash exists as a key in `contract_class_changes` (i.e., it has a non-zero compiled class hash entry). Concretely:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` when resolving a class for execution, and closes the gap noted in the TODO comment. [4](#0-3) 

---

### Proof of Concept

1. **Attacker deploys** `VaultContract` — a contract that accepts ERC-20 deposits from users and stores balances in its storage.
2. **Users deposit** funds; `VaultContract` storage now holds significant value.
3. **Attacker sends** an invoke transaction calling `VaultContract.__execute__`, which internally calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
4. **OS executes** `execute_replace_class`: no validation is performed; `contract_state_changes` is updated with `class_hash = 0xdeadbeef` for `VaultContract`. Transaction succeeds and state is committed.
5. **Any subsequent call** to `VaultContract` (withdraw, transfer, etc.) reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0`. Then `find_element(..., key=0)` panics because no compiled class with hash `0` exists.
6. **Result:** `VaultContract` is permanently unexecutable. All user funds are frozen with no recovery mechanism. [5](#0-4) [6](#0-5)

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
