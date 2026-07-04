### Title
Missing Validation of Declared Class Hash in `execute_replace_class` Allows Permanent Contract Lockout - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash supplied by a contract corresponds to a previously declared class. This is structurally identical to the reported bug: a privileged state-update operation (replacing a contract's class) accepts an arbitrary value without enforcing a required invariant (that the class must be declared), leaving the contract permanently inaccessible and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads the requested `class_hash` directly from the `ReplaceClassRequest` and writes it into the contract's `StateEntry` without any check that the hash corresponds to a declared class:

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

The TODO comment at line 898 explicitly acknowledges the missing check. [1](#0-0) 

The downstream consequence is in `execute_entry_point`, which resolves the class hash to a compiled class via `dict_read` on `contract_class_changes` followed by `find_element` over the compiled class facts bundle:

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

If the class hash written by `replace_class` is not declared, `dict_read` returns `0` (the default for an uninitialized dict entry), and `find_element` with key `0` will fail to locate a valid compiled class. The contract becomes permanently uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once the OS proof is accepted by L1 with the undeclared class hash committed into the global state root, the contract's class hash on-chain is irrevocably set to an undeclared value. No future transaction can successfully execute any entry point of that contract, because the OS cannot resolve the class hash to a compiled class. Any ERC-20 balances, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path.

---

### Likelihood Explanation

**Medium.** The `replace_class` syscall is callable by any contract from its own execution context — no privileged role is required. A contract that holds user funds (e.g., a multisig, escrow, or token vault) can call `replace_class` with an arbitrary felt value. This can happen:

1. **Accidentally**: a contract developer passes an incorrect class hash (e.g., a Sierra hash instead of the compiled class hash, or a hash of an undeclared class).
2. **Maliciously**: a contract with a backdoor or a rug-pull mechanism deliberately calls `replace_class(0xdeadbeef)` to lock user funds before an exit.
3. **Via a malicious sequencer**: a sequencer can include a transaction that triggers `replace_class` with an undeclared hash; the OS will prove it without objection.

---

### Recommendation

In `execute_replace_class`, before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` with a non-zero compiled class hash. Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero, mirroring the lookup already performed in `execute_entry_point`. This resolves the acknowledged TODO at line 898.

---

### Proof of Concept

1. Deploy a contract `Vault` that holds user ERC-20 balances.
2. `Vault` contains a function `lock()` that calls the `replace_class` syscall with `class_hash = 0x1` (an undeclared hash).
3. An attacker (or the contract owner) invokes `lock()`.
4. The OS's `execute_replace_class` accepts the syscall: no validation of `0x1` against declared classes is performed. [3](#0-2) 
5. `dict_update` writes `StateEntry(class_hash=0x1, ...)` for `Vault`'s address into `contract_state_changes`. The block is proven and the state root is committed to L1.
6. In any subsequent block, a user calls `Vault.withdraw()`. The OS calls `execute_entry_point` for `Vault`, reads `class_hash = 0x1`, calls `dict_read` on `contract_class_changes` → returns `0` (undeclared), then `find_element(..., key=0)` fails to find a compiled class. [4](#0-3) 
7. The contract is permanently inaccessible. All user balances stored in `Vault` are frozen forever.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-915)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-177)
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
    }
```
