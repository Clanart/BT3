### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

`execute_replace_class` immediately commits an arbitrary class hash to the contract state without verifying that the hash corresponds to a declared class. This is directly analogous to the reported bug where stake cache is updated before P-Chain confirmation: in both cases, an unvalidated value is written to a state store that downstream logic treats as authoritative. Any contract can call `replace_class` with an undeclared class hash, permanently freezing itself and any funds it holds.

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` performs a `dict_update` on `contract_state_changes` with the caller-supplied `class_hash` without checking whether that hash exists in `contract_class_changes` (the declared-class registry): [1](#0-0) 

The developer-acknowledged TODO at line 898 confirms the check is missing:

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
```

After this update, the contract's `class_hash` in the global state is the undeclared value. On any subsequent call to the contract, `execute_entry_point` performs: [2](#0-1) 

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // ← undeclared hash
);
// compiled_class_hash == 0 (dict default)
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // ← find_element(key=0) panics
);
```

`dict_read` returns `0` (the dict default) for any key not in `contract_class_changes`. `find_element` with `key=0` then fails with an assertion error because no compiled class with hash `0` exists in the bundle. The contract becomes permanently unexecutable.

### Impact Explanation

Any funds held by the frozen contract — ERC-20 balances, ETH, or any other assets stored in its storage — are permanently inaccessible. The only way to change the class hash back would be to call `replace_class` again, but that requires executing the contract, which is now impossible. This satisfies **Critical. Permanent freezing of funds**.

### Likelihood Explanation

The attack path requires only that a contract deployer include a `replace_class` call with an arbitrary felt value in their contract's logic. No privileged access, leaked key, or external dependency is needed. The sequencer's pre-inclusion simulation will see the `replace_class` call succeed (it has no error path), so the transaction is included. The freezing effect is irreversible once committed to the state root.

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify it exists in `contract_class_changes`:

```cairo
func execute_replace_class{..., contract_class_changes: DictAccess*, ...}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // Verify the class is declared.
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
    if (compiled_class_hash == 0) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    // Proceed with state update only after validation.
    ...
}
```

This mirrors the fix recommended in the external report: update the cache only after the external system (P-Chain / declared-class registry) confirms the value.

### Proof of Concept

1. Deploy a contract `VictimVault` that holds user funds and exposes a `freeze()` entry point:
   ```cairo
   @external
   func freeze() {
       // 0xdeadbeef... is not a declared class hash
       replace_class(class_hash=0xdeadbeef);
       return ();
   }
   ```
2. Users deposit funds into `VictimVault`.
3. Attacker calls `freeze()`. The OS executes `execute_replace_class` with `class_hash=0xdeadbeef`. No validation is performed; `dict_update` writes the undeclared hash to `contract_state_changes`.
4. The transaction is included in the block. The state root now encodes `VictimVault.class_hash = 0xdeadbeef`.
5. Any subsequent call to `VictimVault` reaches `execute_entry_point`, which does `dict_read(key=0xdeadbeef)` → returns `0`, then `find_element(key=0)` → assertion failure. The OS cannot generate a proof for any block containing such a call.
6. The sequencer excludes all calls to `VictimVault`. All deposited funds are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
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
