### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the replacement class hash corresponds to a declared contract class. Any contract can replace its own class with an arbitrary undeclared hash. Once committed to L1 state, future calls to that contract cause the OS to panic (unable to resolve the compiled class), permanently freezing any funds held by the contract with no remediation path.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` accepts any `class_hash` value from the syscall request and writes it directly into `contract_state_changes` without checking whether that hash is a declared class:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges the missing check. The state change is committed to L1 as part of the block's state update.

In any subsequent block, when a transaction calls the affected contract, `execute_entry_point` resolves the class:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // the undeclared hash H
);
// compiled_class_hash == 0 (key not in dict)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // key == 0, not found → OS panics
);
``` [2](#0-1) 

`find_element` is a hint-driven search that panics when the key is absent. Because `compiled_class_hash` resolves to `0` for an undeclared class, and no compiled class with hash `0` exists, the OS panics. The prover cannot generate a valid proof for any block containing a call to the affected contract. The sequencer is permanently unable to include such transactions, and all funds in the contract are frozen with no on-chain remediation.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once the replacement class hash is committed to L1 state, the contract is irrecoverably broken at the OS level. No upgrade, governance action, or sequencer configuration can restore the contract's executability, because the invalid class hash is now the canonical on-chain state. Any ERC-20 balances, collateral, or other assets held by the contract are permanently inaccessible.

---

### Likelihood Explanation

The attack surface is broad:

1. **Direct self-inflicted path**: A contract deployer (explicitly listed as a valid attacker class) deploys a contract, then calls `replace_class` with an arbitrary undeclared hash. If the contract holds third-party funds (e.g., a DEX pool, vault, or escrow), those funds are frozen.

2. **Indirect path via contract vulnerability**: If a widely-used contract (holding many users' funds) has any reentrancy, access-control, or logic flaw that allows an external caller to trigger `replace_class` with an attacker-controlled hash, an unprivileged user can permanently freeze all funds in that contract. The OS's missing validation is the necessary root cause that makes the freeze irreversible.

The missing check is acknowledged by the development team (TODO comment), confirming awareness of the gap.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the hash is present in `contract_class_changes` with a non-zero compiled class hash:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation that `execute_entry_point` implicitly relies on and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

1. Attacker deploys **Contract A** (a vault holding user funds). Contract A exposes a function `break_self()` that calls `replace_class(0xdeadbeef)`, where `0xdeadbeef` is not a declared class hash.
2. Attacker calls `break_self()`. The OS processes `execute_replace_class` without validation and writes `class_hash = 0xdeadbeef` into `contract_state_changes` for Contract A's address.
3. The block is proven and the state update (including `class_hash = 0xdeadbeef` for Contract A) is committed to L1.
4. In any future block, a transaction calling Contract A reaches `execute_entry_point`:
   - `dict_read(key=0xdeadbeef)` on `contract_class_changes` returns `0` (undeclared).
   - `find_element(..., key=0)` panics — no compiled class with hash `0` exists.
5. The OS cannot produce a valid proof for any block containing a call to Contract A.
6. The sequencer permanently excludes all transactions targeting Contract A.
7. All funds deposited in Contract A are permanently frozen with no recovery path. [3](#0-2) [4](#0-3)

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
