### Title
Missing Class Hash Validation in `execute_replace_class` Allows Silent State Corruption Leading to Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` is non-zero or corresponds to a declared class. The OS silently accepts any value — including `0` or any undeclared hash — and commits it to the contract state. This is the direct analog of the `transferOwnership` silent-failure pattern: a required input check is absent, so an invalid value passes through without error, permanently corrupting the contract's state and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), after deducting gas, the function reads the caller-supplied `class_hash` and immediately writes it to `contract_state_changes` with no validation whatsoever:

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

Two checks are absent:

1. **No zero-value guard**: `class_hash = 0` is accepted silently. The analog of `require(newOwner != 0x0)` is entirely missing.
2. **No declared-class guard**: The TODO comment (dated `1/1/2026`, already past as of today `2026-07-03`) explicitly acknowledges that the OS never verifies the new hash exists in `contract_class_changes` or the compiled class facts bundle.

Compare this with `execute_declare_transaction`, which does enforce `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`: [2](#0-1) 

And with `deploy_contract`, which enforces `assert_not_zero(...)` on reserved addresses before writing state: [3](#0-2) 

`execute_replace_class` has no equivalent guard.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a contract's class hash is set to `0` (or any undeclared hash) via `replace_class`, the state is committed. On any subsequent call to that contract, `execute_entry_point` executes:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // = 0
);
// compiled_class_hash = 0 (default dict value, never declared)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,   // = 0, not present
);
``` [4](#0-3) 

`find_element` (unlike `search_sorted_optimistic`) panics with a Cairo assertion failure when the key is absent. The contract becomes permanently uncallable. Any ERC-20 balances, ETH, or escrowed assets stored in that contract's storage are irretrievably frozen on-chain. The state root has been updated to reflect the corrupted class hash, and there is no recovery path within the protocol.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context — no privileged role is required. The realistic attack path is:

- A legitimate contract (e.g., a shared escrow, token vault, or multisig) contains a reentrancy or access-control bug that allows an attacker to trigger `replace_class` with an attacker-controlled argument.
- Because the OS performs **no validation**, the OS silently commits `class_hash = 0` to state.
- The OS is the last line of defense; without this check, a contract-level bug escalates from "recoverable revert" to "permanent fund freeze."

Additionally, a malicious contract author can deliberately deploy a contract, attract user funds into it (e.g., as a fake yield vault), then call `replace_class(0)` to permanently lock deposited funds — a rug-pull variant that is irreversible at the protocol level.

The overdue TODO comment (`1/1/2026`) confirms the developers identified this gap but it remains unaddressed.

---

### Recommendation

Add the following two guards at the start of `execute_replace_class`, immediately after reading `class_hash`:

```cairo
let class_hash = request.class_hash;

// Guard 1: reject zero class hash (analog of require(newOwner != 0x0))
with_attr error_message("Invalid class hash: zero is not allowed.") {
    assert_not_zero(class_hash);
}

// Guard 2: reject undeclared class hash
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the pattern already used in `execute_declare_transaction` and `deploy_contract`.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts deposits and exposes a public `brick()` function that calls `replace_class(0)`.
2. Victims deposit funds into `MaliciousVault`.
3. Attacker calls `brick()`. The transaction is included in a block.
4. The OS processes `execute_replace_class` with `class_hash = 0`. No assertion fires. `dict_update` writes `class_hash = 0` to `contract_state_changes`.
5. The block is proven and the state root is updated. `MaliciousVault` now has `class_hash = 0` on-chain.
6. Any subsequent call to `MaliciousVault` (e.g., a victim trying to withdraw) reaches `execute_entry_point`, which calls `find_element(..., key=0)`. Since class `0` was never declared, `find_element` panics.
7. All funds in `MaliciousVault` are permanently frozen. No withdrawal, upgrade, or recovery is possible within the protocol.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-49)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
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
