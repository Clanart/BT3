### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by the caller corresponds to a previously declared contract class. The OS unconditionally writes any arbitrary felt value as the contract's new class hash. Any contract that calls `replace_class` with an undeclared hash — whether due to a bug, a failed upgrade race, or a malicious self-call — will have its class permanently set to a non-existent class, making the contract permanently uncallable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check against `contract_class_changes` (the dictionary of declared classes):

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
```

The developer-acknowledged TODO at line 898 confirms the missing check is known but unimplemented. [1](#0-0) 

By contrast, the `execute_declare_transaction` function correctly enforces `prev_value=0` to prevent double-declaration, and the `deploy_contract` function enforces `UNINITIALIZED_CLASS_HASH` checks before writing state. [2](#0-1)  The `replace_class` path has no equivalent guard.

The `contract_class_changes` dictionary, which maps class hashes to compiled class hashes, is the authoritative record of declared classes. [3](#0-2)  `execute_replace_class` never consults it.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every future call to that contract will fail at class resolution time. There is no recovery path: `replace_class` is a one-way write, and no syscall exists to restore the previous class hash. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. This matches the StarkNet bounty's "Critical — Permanent freezing of funds" impact category.

---

### Likelihood Explanation

**Low-to-Medium.** The most realistic trigger is an upgrade race condition: a contract calls `replace_class(new_class_hash)` in the same block where the corresponding `declare` transaction is also submitted, but the declare fails or is excluded by the sequencer. The OS will still accept the `replace_class` call and commit the undeclared hash to state. A malicious contract can also trigger this deliberately against itself (e.g., as part of a griefing or rug-pull scenario where the deployer intentionally bricks the contract after collecting user funds). Because `replace_class` is an unprivileged syscall callable by any executing contract, no special role or key is required.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, `execute_replace_class` must verify that `class_hash` exists as a key in `contract_class_changes` with a non-zero compiled class hash. Concretely, a `dict_read` on `contract_class_changes` keyed by `request.class_hash` should be performed, and the result must be asserted non-zero (i.e., `assert_not_zero(compiled_class_hash)`). This mirrors the existing guard in `execute_declare_transaction` and closes the gap identified in the TODO comment.

---

### Proof of Concept

1. Attacker deploys contract `C` holding user funds (e.g., an ERC-20 vault).
2. Attacker's contract calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not present in `contract_class_changes`).
3. The OS executes `execute_replace_class`:
   - Reads `request.class_hash = 0xdeadbeef`. [4](#0-3) 
   - Performs **no lookup** in `contract_class_changes`.
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`. [5](#0-4) 
4. The block is proven and finalized. Contract `C` now has class hash `0xdeadbeef` on-chain.
5. Any subsequent call to `C` (e.g., `withdraw`) fails at class resolution — the class does not exist.
6. All funds in `C`'s storage are permanently frozen with no recovery mechanism.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
