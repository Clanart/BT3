### Title
Missing Declared-Class Validation in `replace_class` Syscall Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not verify that the new class hash supplied by a contract is actually a declared class. The OS accepts any arbitrary felt value as the replacement class hash. A malicious contract deployer can exploit this to permanently freeze all funds held by a contract by replacing its class with an undeclared hash, rendering the contract permanently non-callable.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash corresponds to a declared class:

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
```

The `TODO` comment explicitly acknowledges the missing check. [1](#0-0) 

After the replacement, any future call to the contract triggers `execute_entry_point`, which performs:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — returns `0` (default) for an undeclared hash.
2. `find_element(... key=compiled_class_hash)` — fails to find a `CompiledClassFact` with hash `0`, causing an unrecoverable assertion failure. [2](#0-1) 

The contract becomes permanently non-callable, and all funds held within it are permanently frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other assets held in the storage of the affected contract become permanently inaccessible. No recovery path exists because the class hash stored in state is invalid and the OS has no mechanism to revert it after the block is proven.

---

### Likelihood Explanation

**Medium.** The attack requires a malicious contract deployer who:
1. Deploys a contract (e.g., a vault, token, or DeFi protocol).
2. Attracts user deposits.
3. Calls `replace_class` with an arbitrary undeclared hash (e.g., `1`).

This is a realistic rug-pull vector. The OS is the enforcement layer; if it does not validate the class hash, a valid proof can be generated for this invalid state transition regardless of what the sequencer/blockifier checks at a higher layer.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to state, verify that the hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Specifically, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` and makes the OS the authoritative enforcer of the invariant.

---

### Proof of Concept

1. Attacker deploys `VaultContract` with a valid class hash `C`. Users deposit 1000 STRK.
2. `VaultContract` exposes a function `freeze()` that internally calls the `replace_class` syscall with `new_class_hash = 0xdeadbeef` (not declared).
3. Attacker calls `freeze()`. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No declared-class check is performed. [3](#0-2) 
   - `contract_state_changes[VaultContract.address].class_hash = 0xdeadbeef` is committed to state.
4. Block is proven and accepted on L1.
5. Any subsequent call to `VaultContract` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0`.
   - `find_element(..., key=0)` → no matching `CompiledClassFact` found → execution fails. [4](#0-3) 
6. The 1000 STRK are permanently frozen. No withdrawal function can ever be called.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-176)
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
```
