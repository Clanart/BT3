### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that the hash corresponds to a previously declared contract class. A contract deployer can exploit this to replace their contract's class with an undeclared hash, permanently bricking the contract and freezing all funds held within it. The OS itself contains a `TODO` comment acknowledging this missing check.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

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

The `class_hash` value is taken directly from the user-controlled syscall request (`request.class_hash`) with no validation that it exists in `contract_class_changes` (the declared class registry). The OS unconditionally writes the arbitrary hash into the contract's `StateEntry`. [2](#0-1) 

When the contract is subsequently called, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [3](#0-2) 

If the class hash set by `replace_class` is undeclared, `dict_read` returns 0 (the default for an uninitialized dict entry), and `find_element` fails to locate a compiled class. The contract becomes permanently unexecutable — no entry point can ever be dispatched — while its storage and any token balances remain locked in state.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds (ERC-20 balances, ETH, or other assets) held in the storage of a contract whose class hash has been replaced with an undeclared value are permanently inaccessible. No withdrawal, transfer, or recovery function can be called because the OS cannot dispatch any entry point for that contract. The state root still commits to the contract's storage, but the execution layer is permanently blocked.

---

### Likelihood Explanation

The `replace_class` syscall is available to any executing contract. A contract deployer — explicitly listed as a valid attacker in the protocol's access model — can:

1. Deploy a contract that accepts user deposits (e.g., a staking pool, escrow, or DeFi vault).
2. After users deposit funds, call `replace_class` with an arbitrary undeclared hash (e.g., `0x1`).
3. The OS writes the invalid class hash into the contract's `StateEntry` with no resistance.

This is directly analogous to H10: a service provider changes a critical parameter (class hash instead of `deployerCut`) immediately before users attempt to interact, causing irreversible loss. No timelock, no validation, and no on-chain warning exists to protect depositors.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, the OS must verify that the hash is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, `execute_replace_class` should perform a `dict_read` on `contract_class_changes` for `request.class_hash` and assert the result is non-zero before proceeding. The existing `TODO` comment at line 898 already identifies this gap; it must be resolved before the syscall is considered safe. [4](#0-3) 

---

### Proof of Concept

1. **Deploy** a contract `Vault` that accepts ERC-20 deposits from users and stores balances in its own storage.
2. **Users deposit** funds; the Vault's storage now holds their balances.
3. **Attacker (deployer) calls** a function in `Vault` that invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not present in `contract_class_changes`).
4. The OS executes `execute_replace_class`: no declared-class check is performed; `contract_state_changes[Vault_address].class_hash` is set to `0xdeadbeef`.
5. In all subsequent blocks, any transaction targeting `Vault` reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`, then `find_element(..., key=0)` → no compiled class found → execution fails unconditionally.
6. **All user funds are permanently frozen.** The state root commits to the Vault's storage, but no entry point can ever execute to move those funds.

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
