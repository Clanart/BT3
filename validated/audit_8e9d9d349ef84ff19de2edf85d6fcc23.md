### Title
Missing Declared-Class Validation in `replace_class` Syscall Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt as a new class hash without verifying that the hash corresponds to a declared contract class. An unprivileged contract deployer can exploit this to permanently freeze all funds held by a contract by replacing its class with an undeclared hash, rendering the contract permanently uncallable.

---

### Finding Description

The `replace_class` syscall implementation in `syscall_impls.cairo` updates a contract's class hash to whatever value the caller provides, with no validation that the new hash is a declared class:

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

After `replace_class` succeeds with an invalid hash, any subsequent call to that contract reaches `execute_entry_point`, which performs:

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

`dict_read` on an undeclared class hash returns 0 (the default). `find_element` with `key=0` will fail with a Cairo assertion if no compiled class with hash 0 exists in the bundle, making any block containing a call to the broken contract unprovable. The sequencer is therefore forced to permanently exclude all calls to that contract, freezing any funds it holds.

The analog to the external report is direct: just as the Redpanda API service holds admin-level power (user/ACL configuration) without proper authorization checks, the `replace_class` syscall holds admin-level power over a contract's identity (its class hash) without verifying the new class is valid — a privileged operation with no guard.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:
- Every subsequent call to the contract causes `find_element` to fail at the OS level (not a normal revert).
- The sequencer cannot include any transaction that calls the contract without making the block unprovable.
- All ERC-20 balances, NFTs, or protocol state stored in the contract's storage become permanently inaccessible.
- There is no recovery path: the class hash is committed to the state tree and cannot be corrected without a protocol upgrade.

---

### Likelihood Explanation

**Medium.**

The attack requires:
1. Deploying a contract (permissionless on StarkNet).
2. Calling `replace_class` from within that contract with an arbitrary felt (e.g., `1` or any random value not in the declared class set).

No special privileges, leaked keys, or operator cooperation are needed. The attacker controls the contract code and can craft the `replace_class` call directly. The only prerequisite is that the contract holds funds worth targeting, which is achievable by advertising the contract as a legitimate service before executing the attack.

---

### Recommendation

Before updating `contract_state_changes`, verify that `request.class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). The check should mirror the lookup already performed in `execute_entry_point`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(
    key=request.class_hash
);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This resolves the acknowledged TODO at line 898 of `syscall_impls.cairo`. [3](#0-2) 

---

### Proof of Concept

1. **Deploy** a Cairo contract `VaultAttacker` that:
   - Accepts deposits (stores balances in storage).
   - Exposes a public `attack()` function that calls `replace_class(0xdeadbeef)` — an arbitrary undeclared felt.

2. **Attract deposits**: Advertise the contract; users deposit tokens.

3. **Call `attack()`**: The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`. No validation is performed. The state entry for `VaultAttacker`'s address is updated with `class_hash = 0xdeadbeef`. [4](#0-3) 

4. **Subsequent calls fail**: Any transaction calling `VaultAttacker` reaches `execute_entry_point`, which calls `dict_read` on `0xdeadbeef` (returning 0), then `find_element` with `key=0`. Since no compiled class has hash 0, `find_element` panics. The sequencer cannot include such transactions. [5](#0-4) 

5. **Result**: All deposited funds are permanently frozen. Withdrawals, transfers, and any interaction with the contract are impossible.

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
