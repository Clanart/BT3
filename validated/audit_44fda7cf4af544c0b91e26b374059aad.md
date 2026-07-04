### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value from a contract without verifying that the hash corresponds to a class that has actually been declared on-chain. This is an insufficient-validation analog to the external report's insufficient-authorization pattern: just as any manager could grant new managers without an owner check, any contract can replace its own class with an undeclared (invalid) hash without an OS-level check. The result is that the contract becomes permanently un-executable, freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested `class_hash` from the syscall request and immediately writes it into `contract_state_changes` with no check that the hash exists in `contract_class_changes` (the declared-class registry):

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
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges the missing check. The OS developers are aware this validation is absent.

When a subsequent transaction calls the affected contract, `execute_entry_point` performs:

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

If `class_hash` was never declared, `dict_read` returns 0 (undeclared), and `find_element` — which panics on a missing key — causes the OS execution to abort. The block becomes unprovable for any transaction targeting that contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:
1. Every future call to that contract causes the OS to abort during `find_element`.
2. The sequencer must permanently exclude all transactions targeting that contract to keep blocks provable.
3. Any ETH, STRK, or ERC-20 tokens held in the contract's storage are irrecoverably frozen — no withdrawal, transfer, or administrative function can ever execute again.

This matches the "Critical — Permanent freezing of funds" impact in the allowed scope.

---

### Likelihood Explanation

The attack is reachable by any unprivileged user:

- StarkNet is permissionless: any user can declare a class and deploy a contract.
- The `replace_class` syscall is available to any executing contract — no special role is required.
- A malicious contract author can publish a contract that exposes a public function calling `replace_class(undeclared_hash)`. Once other users deposit funds, the author (or any caller of that function) triggers the freeze.
- The OS-level missing check means the proof is accepted even though the resulting state is permanently broken.

No trusted role, leaked key, or network-level attack is required.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that it is present in `contract_class_changes` (i.e., it has been declared):

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

+   // Verify the new class hash has been declared.
+   let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
+   assert_not_zero(compiled_class_hash);

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    ...
}
```

This mirrors the check already enforced in `execute_declare_transaction`:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

---

### Proof of Concept

1. **Attacker declares** a legitimate class `C_legit` and deploys contract `Vault` using it. `Vault` accepts deposits from other users and exposes a public `freeze()` function.

2. **`freeze()` implementation** calls the `replace_class` syscall with `class_hash = 0xdeadbeef` — a felt that has never been declared.

3. **OS processes** the `replace_class` syscall via `execute_replace_class`. Because there is no check against `contract_class_changes`, the OS writes `class_hash = 0xdeadbeef` into `contract_state_changes` for `Vault`. The block is proven and accepted on L1.

4. **Any subsequent transaction** targeting `Vault` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → `0` (never declared).
   - `find_element(..., key=0)` → **panic** (element not found).
   - The block containing that transaction cannot be proven.

5. **Sequencer is forced** to permanently exclude all transactions to `Vault`. All deposited funds are frozen with no recovery path. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
