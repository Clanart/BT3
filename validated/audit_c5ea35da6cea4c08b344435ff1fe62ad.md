### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by the caller corresponds to a previously declared contract class. A malicious contract can call `replace_class` with an arbitrary undeclared felt value, permanently rendering the contract uncallable and freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts the caller-supplied `class_hash` from the syscall request and writes it directly into `contract_state_changes` without any check that the hash exists in the declared class registry (`contract_class_changes`): [1](#0-0) 

The developer-acknowledged TODO at line 898 explicitly states the missing guard:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

After this write, the contract's `StateEntry.class_hash` in `contract_state_changes` is set to an arbitrary, undeclared value.

When any subsequent transaction attempts to call this contract, `execute_entry_point` reads the class hash from state and performs a `dict_read` on `contract_class_changes` to resolve it to a `compiled_class_hash`: [2](#0-1) 

Because the class hash was never declared, `dict_read` returns `0`. The subsequent `find_element` call then searches `compiled_class_facts_bundle` for a compiled class with hash `0`. If no such entry exists, `find_element` fails with a hard assertion, making the block unprovable for any block that includes a call to this contract.

The contract is permanently bricked: its class hash points to nothing, every future call to it fails at the OS proof level, and all funds stored in its storage are irrecoverably frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, NFT, or protocol-managed asset stored in the contract's storage becomes permanently inaccessible. No withdrawal, transfer, or recovery path exists once the class hash is overwritten with an undeclared value, because the OS will never successfully execute an entry point for that contract again.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context — no privileged role is required. A malicious contract developer can:

1. Deploy a contract and attract user deposits.
2. Trigger an internal call to `replace_class` with an arbitrary undeclared felt (e.g., `0xdeadbeef`).
3. The OS accepts the syscall with no validation.
4. The contract is permanently bricked; all deposited funds are frozen.

The entry path is fully permissionless and requires only a standard `invoke` transaction from the attacker.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, assert that the supplied `class_hash` maps to a non-zero entry in `contract_class_changes` (i.e., it has been declared). This is exactly what the existing TODO comment calls for:

```cairo
// Enforce that the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the check already performed implicitly in `execute_entry_point` but must be enforced at the point of replacement to prevent invalid state from being committed.

---

### Proof of Concept

1. Attacker deploys `MaliciousEscrow` — a contract that accepts user token deposits.
2. Users deposit funds; the contract's storage now holds balances.
3. Attacker sends an `invoke` transaction calling `MaliciousEscrow::freeze()`, which internally issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).
4. The OS processes `execute_replace_class`:
   - Gas is deducted.
   - `contract_state_changes` is updated: `MaliciousEscrow.class_hash = 0xdeadbeef`.
   - No declared-class check is performed (line 898 TODO).
5. In any subsequent block, a user submits a withdrawal transaction targeting `MaliciousEscrow`.
6. The OS calls `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(..., key=0)` → hard assertion failure; block is unprovable.
7. The sequencer cannot include any call to `MaliciousEscrow` in a provable block.
8. All user funds are permanently frozen. [3](#0-2) [4](#0-3)

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
