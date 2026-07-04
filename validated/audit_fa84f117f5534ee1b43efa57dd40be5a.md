### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying it has been declared. A contract can call `replace_class` with an undeclared class hash, permanently freezing itself and any funds it holds, because subsequent calls to the contract will cause the OS to abort when it cannot resolve the invalid class hash to a compiled class.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function updates a contract's class hash in `contract_state_changes` with no validation that the new hash corresponds to a declared class. The code itself acknowledges this with an explicit TODO: [1](#0-0) 

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

The OS commits this state transition unconditionally. When the contract is subsequently called, `execute_entry_point` resolves the class hash through two steps:

**Step 1** — look up the compiled class hash: [2](#0-1) 

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

If the class hash was never declared, `dict_read` returns `0` (the Cairo dict default). `find_element` is then called with `key=0`, which is not a valid compiled class hash. This causes the Cairo program to abort, making the block unprovable. The sequencer is forced to exclude every future transaction that calls the frozen contract, rendering it permanently inaccessible.

The `contract_class_changes` dict is populated only when a class is declared: [3](#0-2) 

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

`replace_class` writes only to `contract_state_changes` (the per-contract state), never to `contract_class_changes` (the class registry). There is therefore no path by which an undeclared hash passed to `replace_class` can later be resolved.

---

### Impact Explanation

Any ERC-20 tokens or ETH stored in the contract's storage become permanently inaccessible. The contract's entry points can never be executed again because every attempt causes the OS to abort. This is **Critical — Permanent Freezing of Funds**, which is within the allowed impact scope.

---

### Likelihood Explanation

The attack path requires only the ability to deploy a contract — no privileged role, no operator access, no key compromise. A malicious actor deploys a contract that appears legitimate (vault, multisig, DeFi protocol), attracts user deposits, and then triggers `replace_class` with an arbitrary undeclared hash. The OS has no defense at the protocol level; the missing check is explicitly noted as a TODO in the production code.

---

### Recommendation

Before writing the new class hash to `contract_state_changes` in `execute_replace_class`, verify that the hash exists in `contract_class_changes`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the existing pattern used in `execute_entry_point` and ensures the OS only accepts valid class hash replacements.

---

### Proof of Concept

1. Deploy `MaliciousVault` — a contract with a public `deposit` function and a hidden `freeze` function callable only by the deployer. The `freeze` function calls `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is not declared.
2. Users deposit funds into `MaliciousVault`.
3. Deployer calls `freeze`. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No declared-class check is performed (the TODO confirms this)
   - `contract_state_changes` is updated: `MaliciousVault.class_hash = 0xdeadbeef`
4. In any subsequent block, a user calls `MaliciousVault.withdraw`:
   - `execute_entry_point` calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`
   - `find_element(compiled_class_facts, key=0)` → aborts (no compiled class with hash `0`)
   - The block is unprovable; the sequencer drops the transaction
5. All user funds in `MaliciousVault` are permanently frozen.

Root cause is at: [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
