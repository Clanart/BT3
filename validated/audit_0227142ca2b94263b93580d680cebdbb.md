### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash provided by a contract corresponds to a previously declared contract class. This allows any contract to replace its class with an arbitrary, undeclared class hash, permanently bricking the contract and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` and directly updates the contract's `StateEntry` in `contract_state_changes` without verifying that the new class hash has ever been declared on-chain:

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

The TODO comment at line 898 explicitly acknowledges this missing validation. [2](#0-1) 

When a subsequent call is made to the affected contract, `execute_entry_point` performs a `dict_read` on `contract_class_changes` for the invalid class hash. Since the hash was never declared, the dict returns the default value `0`. The OS then calls `find_element` to locate a compiled class with hash `0`:

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
``` [3](#0-2) 

`find_element` asserts the element is found; if no compiled class with hash `0` exists (which is always the case), the Cairo VM fails with an assertion error. The contract becomes permanently uncallable — no entry point can ever be executed again.

This is the direct analog to the UniswapV2 slippage bug: just as `amount0In`/`amount1In` were computed from manipulable pool state without a bound check, here the OS accepts a class hash from mutable contract-controlled input (`request.class_hash`) without validating it against the set of declared classes — the "slippage protection" equivalent.

---

### Impact Explanation

Any funds held by a contract that has replaced its class with an undeclared hash are **permanently frozen**. The contract cannot be called (no entry point can be dispatched), so there is no mechanism to withdraw, transfer, or recover the funds. This matches the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack is reachable by any unprivileged user who can deploy a contract. The `execute_replace_class` syscall is callable by any executing contract with no privilege requirement. A realistic attack path:

1. Attacker deploys a contract (e.g., a fake yield vault or bridge) designed to attract user deposits.
2. Users deposit funds into the contract.
3. The contract calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is an arbitrary undeclared hash.
4. The OS accepts the call and updates the contract's class hash in state.
5. All future calls to the contract fail at the OS level — funds are permanently frozen.

Additionally, legitimate contracts with upgrade logic bugs could accidentally trigger this condition without malicious intent.

---

### Recommendation

Add a validation check inside `execute_replace_class` to verify that `request.class_hash` exists in `contract_class_changes` (i.e., has been declared). Concretely, before updating the state entry, assert:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed in `execute_entry_point` and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

1. Attacker deploys contract `C` whose constructor or any callable function issues `replace_class(0xdeadbeef)`.
2. Users deposit funds into `C` (e.g., via a `deposit` entry point).
3. Attacker triggers the `replace_class(0xdeadbeef)` call. The OS executes `execute_replace_class`, skips the missing declared-class check, and writes `class_hash=0xdeadbeef` into `C`'s `StateEntry` in `contract_state_changes`. [4](#0-3) 
4. In any subsequent block, a call to `C` reaches `execute_entry_point`. `dict_read(contract_class_changes, 0xdeadbeef)` returns `0` (undeclared). `find_element(..., key=0)` fails with an assertion error. [5](#0-4) 
5. The sequencer excludes all calls to `C` from future blocks. `C` is permanently uncallable. All user funds deposited in `C` are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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
