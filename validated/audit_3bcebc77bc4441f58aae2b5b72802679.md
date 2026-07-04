### Title
Missing Validation of `class_hash` in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` value from user-controlled calldata and writes it directly to the contract's state entry without any validation. There is no check that the new class hash is non-zero and no check that it corresponds to a previously declared class. This is structurally identical to the external report's vulnerability class: a critical value obtained from an untrusted source is used in a state-mutating operation without existence/validity checks. The consequence is permanent, irreversible freezing of all funds held by any contract that invokes `replace_class` with an undeclared or zero class hash.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the new class hash directly from the syscall request struct and immediately commits it to the contract state:

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

The developer-inserted TODO at line 898 explicitly acknowledges the missing check. No `assert_not_zero` or declared-class membership check is performed on `class_hash` before it is written.

When a subsequent transaction calls into a contract whose on-chain `class_hash` is now 0 (or any undeclared hash), `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // reads 0
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // key = 0, not present
);
``` [2](#0-1) 

`find_element` is not a graceful-failure primitive; it requires the element to exist in the array. Because no compiled class with hash 0 is ever registered, no sequencer can include a call to that contract in any future block without causing the OS proof to fail. The contract becomes permanently uncallable.

The constant `UNINITIALIZED_CLASS_HASH = 0` is defined in `commitment.cairo` and is the sentinel for "no contract deployed here": [3](#0-2) 

Resetting a live contract's class hash to this sentinel value is semantically equivalent to undeploying it while leaving its storage and token balances intact and inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 token balance, ETH, or other asset held in the storage of the affected contract becomes permanently inaccessible. Because the state transition is committed to the Merkle tree and the OS has no upgrade or recovery path for a contract whose class hash is 0, the freeze is irreversible. This matches the allowed impact "Critical. Permanent freezing of funds."

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly accessible StarkNet syscall callable by any contract. The attack requires only that a contract (controlled or influenced by the attacker) invoke `replace_class(0)`. Concrete scenarios:

1. **Attacker-deployed contract**: An attacker deploys a contract, receives funds from users (e.g., a fake yield vault), then calls `replace_class(0)` to freeze all deposited assets.
2. **Reentrancy / callback manipulation**: A contract that calls `replace_class` with a value derived from untrusted external input (e.g., a user-supplied upgrade hash) can be tricked into passing 0.
3. **Buggy upgrade logic**: Any contract that performs an upgrade without validating the new class hash is silently vulnerable to self-inflicted permanent freezing.

No privileged role, leaked key, or operator cooperation is required. The entry path is a standard user-submitted transaction.

---

### Recommendation

Add an `assert_not_zero` check on `class_hash` immediately after reading it from the request, and enforce that the hash corresponds to a previously declared class by verifying its presence in `contract_class_changes`:

```cairo
let class_hash = request.class_hash;

// Guard 1: class hash must be non-zero.
with_attr error_message("replace_class: class_hash must not be zero.") {
    assert_not_zero(class_hash);
}

// Guard 2: class hash must be declared (present in contract_class_changes).
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class_hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the pattern already used in `deploy_contract.cairo` where reserved addresses are rejected via `assert_not_zero` before any state mutation occurs. [4](#0-3) 

---

### Proof of Concept

**Step 1 — Attacker deploys a vault contract** that accepts deposits and exposes an `upgrade(new_class_hash: felt)` entry point that calls `replace_class(new_class_hash)`.

**Step 2 — Users deposit funds** (e.g., STRK tokens) into the vault. The vault's storage now holds non-zero balances.

**Step 3 — Attacker calls `upgrade(0)`** via a standard invoke transaction. The OS executes `execute_replace_class`:

- `request.class_hash = 0` (attacker-supplied)
- No validation is performed (line 896–910 of `syscall_impls.cairo`)
- `dict_update` writes `StateEntry { class_hash: 0, ... }` for the vault's address

**Step 4 — State is committed.** The Merkle tree now records `class_hash = 0` for the vault. `get_contract_state_hash` in `commitment.cairo` treats `class_hash == UNINITIALIZED_CLASS_HASH` as a special case but still commits the storage root, so the funds remain in storage but the contract is logically undeployed. [5](#0-4) 

**Step 5 — Any future call to the vault** causes `execute_entry_point` to call `dict_read(key=0)` on `contract_class_changes`, obtaining compiled class hash 0, then `find_element(..., key=0)` which has no matching entry in the compiled class facts array. The sequencer cannot include such a transaction without making the OS proof unsatisfiable. All deposited funds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L55-61)
```text
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }
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
