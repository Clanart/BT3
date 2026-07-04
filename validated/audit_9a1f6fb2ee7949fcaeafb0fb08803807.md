### Title
Unvalidated Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared contract class. A contract that calls `replace_class` with an undeclared class hash will have its class hash permanently set to an invalid value, making all future calls to that contract unprovable and permanently freezing any funds held within it. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS reads the requested new class hash directly from the syscall request buffer and writes it into `contract_state_changes` without any validation that the hash exists in `contract_class_changes` (i.e., that it was previously declared):

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

The `class_hash` value is fully attacker-controlled — it is whatever the calling contract writes into the syscall segment. There is no range check, no lookup into `contract_class_changes`, and no lookup into the compiled class facts bundle at this point.

When a future transaction calls the affected contract, `execute_entry_point` performs:

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

If the class hash stored in state is undeclared, `dict_read` returns 0 (the default for an uninitialized dict entry), and `find_element` with key `0` will fail to locate a matching compiled class fact, causing the proof for any block that includes a call to the affected contract to be unprovable. The sequencer is therefore permanently unable to include any transaction that touches the contract, and all funds within it are irrecoverably frozen.

The vulnerability class is a **state-transition bypass / missing input validation**: the OS accepts a state mutation (class replacement) without enforcing that the resulting state is valid (new class hash is declared), directly analogous to the external report's pattern of accepting state changes without verifying their legitimacy.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the block containing that `replace_class` call is proven and finalized on L1, the contract's state entry in the global Merkle tree permanently reflects the invalid class hash. No future block can include a successful call to that contract. Any ERC-20 balances, ETH, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path at the protocol level.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract from within its own execution context. Realistic trigger paths include:

1. **Vulnerable upgrade proxy**: A contract exposes a public or permissioned upgrade function that calls `replace_class` with a caller-supplied or externally-sourced class hash. An attacker who can influence that value (e.g., via a reentrancy, a storage collision, or a missing access-control check in the proxy) can supply an undeclared hash.
2. **Buggy contract**: A contract that computes the new class hash from on-chain data (e.g., a storage slot, calldata, or a cross-contract call result) without validating the result before passing it to `replace_class`.
3. **Malicious contract owner**: A contract owner intentionally calls `replace_class` with an invalid hash to freeze user funds (rug-pull variant).

No privileged sequencer or operator role is required. The attacker only needs to be able to trigger execution of a `replace_class` syscall with a controlled hash value, which is achievable by any unprivileged transaction sender who can interact with a vulnerable contract.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, `execute_replace_class` must verify that the hash is present in `contract_class_changes`. Concretely, a `dict_read` on `contract_class_changes` with `key=class_hash` should be performed, and the result must be asserted non-zero (i.e., the class was previously declared). If the class hash is not declared, the syscall should write a failure response instead of updating state. This is exactly what the existing TODO comment calls for:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [3](#0-2) 

The fix should mirror the pattern used in `execute_entry_point`, which reads `contract_class_changes` to resolve a class hash before proceeding.

---

### Proof of Concept

1. Deploy contract `Victim` holding user funds. Its current class hash is `VALID_HASH` (declared).
2. `Victim` exposes a function `upgrade(new_hash)` that calls `replace_class(new_hash)` without validating `new_hash`.
3. Attacker calls `Victim.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips the missing declared-class check (TODO line 898).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
   - Appends a revert-log entry and returns success.
5. The block is proven and finalized. `Victim`'s state entry in the global tree now has `class_hash = 0xdeadbeef`.
6. In any subsequent block, a transaction calling `Victim` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(..., key=0)` → no compiled class with hash `0` exists → proof fails.
7. The sequencer cannot include any call to `Victim`. All funds are permanently frozen.

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
