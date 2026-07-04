### Title
Missing Validation of Class Hash in `execute_replace_class` Allows Permanent Contract Bricking — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in `syscall_impls.cairo` accepts an arbitrary, attacker-controlled class hash from a contract and writes it directly into the on-chain state without verifying that the hash corresponds to a previously declared contract class. This allows any contract to permanently replace its own class hash with an undeclared value, making the contract permanently unexecutable and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class`, the function reads `class_hash` directly from the syscall request and immediately updates `contract_state_changes` with no validation:

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

When `execute_entry_point` subsequently tries to execute a contract whose class hash was replaced with an undeclared value, it performs:

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

Since the undeclared hash has no entry in `contract_class_changes`, `dict_read` returns the default value of `0`. `find_element` then asserts that a `CompiledClassFact` with `hash=0` exists in the prover-supplied array — which it does not — causing the OS to be unable to produce a valid proof for any block containing a call to this contract.

The vulnerability class is identical to the reference report: **state is updated with an unvalidated user-supplied identifier**, with no check that the identifier refers to a valid, registered entity.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a block is committed with a contract's class hash set to an undeclared value, every subsequent attempt to execute that contract will fail at the OS proof-generation level. The contract becomes permanently unexecutable. Any ERC-20 balances, ETH, or other assets held by or managed exclusively through that contract are permanently inaccessible. The state commitment is final and irreversible.

---

### Likelihood Explanation

The `replace_class` syscall is a standard, permissionless syscall available to any executing contract — no privileged role is required. A malicious contract can call it deliberately with an arbitrary felt value. A buggy contract could also trigger it accidentally. The attacker only needs to deploy a contract and invoke `replace_class` with any value not present in `contract_class_changes`. The code path is unconditionally reachable by any unprivileged transaction sender.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that the provided hash corresponds to a declared class by asserting that `contract_class_changes` contains a non-zero compiled class hash for it:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed in `execute_entry_point` and in `execute_declare_transaction` (which enforces `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`). [3](#0-2) 

---

### Proof of Concept

1. Deploy contract `Vault` that holds user funds and exposes a `replace_and_brick()` function.
2. `replace_and_brick()` calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (any felt not present in `contract_class_changes`).
3. `execute_replace_class` accepts the call, skips the missing validation, and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`. [4](#0-3) 
4. The block is committed; the Vault's on-chain class hash is now `0xdeadbeef`.
5. In any subsequent block, a call to `Vault` causes `execute_entry_point` to call `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` → returns `0`. [5](#0-4) 
6. `find_element(..., key=0)` asserts a compiled class fact with hash `0` exists — it does not — the OS cannot generate a valid proof for any block touching `Vault`.
7. All funds in `Vault` are permanently frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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
