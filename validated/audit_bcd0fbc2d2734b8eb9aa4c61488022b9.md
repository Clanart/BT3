### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` from the caller without verifying that the hash corresponds to a previously declared class. An unprivileged contract can call `replace_class` with any felt value as the new class hash. Once the contract's on-chain class pointer is updated to an undeclared hash, every subsequent call to that contract fails irrecoverably at the OS level, permanently freezing any funds held inside it.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads the caller-supplied `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any membership check against the set of declared classes:

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
``` [1](#0-0) 

The in-code `TODO` comment explicitly acknowledges the missing check. The `contract_class_changes` dictionary (which maps `class_hash → compiled_class_hash`) is only populated by successful declare transactions. When `execute_entry_point` later resolves a call to the affected contract, it performs:

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

If `class_hash` was never declared, `dict_read` returns `0` (the default uninitialized value), and `find_element` cannot locate a compiled class fact with hash `0`. The hint-based `find_element` call fails, making it impossible for the prover to produce a valid proof for any block that includes a call to the affected contract. The sequencer is therefore forced to permanently exclude all transactions targeting that contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 tokens, ETH, or other assets held in the storage of the affected contract become permanently inaccessible. No withdrawal, transfer, or administrative function can ever be executed again because the OS cannot produce a valid proof for any call to the contract. The freeze is irreversible: `replace_class` can only be called by the contract itself, and once the class pointer is corrupted the contract can no longer execute any entry point to self-correct.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract whose code invokes it. A malicious contract developer can:

1. Deploy a contract that accepts user deposits (acting as a vault, lending pool, or bridge).
2. Accumulate user funds.
3. Call `replace_class` with an arbitrary undeclared felt (e.g., `1`) as the new class hash.

No privileged role, leaked key, or operator cooperation is required. The attacker only needs to be a contract deployer — an explicitly listed unprivileged entry path. The attack requires a single transaction and is fully deterministic.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the supplied `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared):

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap acknowledged by the existing TODO comment.

---

### Proof of Concept

1. Attacker deploys `VaultContract` (a Cairo 1 contract) that:
   - Accepts ERC-20 deposits from users via a `deposit()` entry point.
   - Exposes an `attack()` entry point that calls `replace_class(class_hash=1)`.

2. Users deposit funds; `VaultContract` accumulates a balance.

3. Attacker calls `attack()`. The OS executes `execute_replace_class` with `class_hash = 1`. Because `1` is not in `contract_class_changes`, no validation fails — the update is written unconditionally.

4. `VaultContract`'s `class_hash` field in `contract_state_changes` is now `1`.

5. Any subsequent transaction targeting `VaultContract` reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, key=1)` → returns `0`. `find_element(..., key=0)` fails; the prover cannot complete the proof.

6. The sequencer must permanently exclude all calls to `VaultContract`. All deposited funds are frozen forever.

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
