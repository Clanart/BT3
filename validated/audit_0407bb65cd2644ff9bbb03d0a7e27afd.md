### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the replacement class hash corresponds to a declared contract class. Any contract can set its own class hash to an arbitrary undeclared felt value. Once committed to the global state root, the contract becomes permanently uncallable, freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads `request.class_hash` from the syscall segment — a value fully controlled by the calling contract — and writes it directly into `contract_state_changes` with no check that the class hash has been declared.

The TODO comment at line 898 explicitly acknowledges this missing guard:

```cairo
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

The downstream pipeline performs no class-hash validation either. `squash_state_changes_inner` in `squash.cairo` simply squashes the storage dict and copies `class_hash` verbatim into the new `StateEntry`:

```cairo
assert [squashed_prev_state] = StateEntry(
    class_hash=prev_state.class_hash, storage_ptr=squashed_storage_ptr, nonce=prev_state.nonce
);
``` [2](#0-1) 

`compute_contract_state_commitment` in `commitment.cairo` then hashes and commits whatever `class_hash` is present in the state entry into the global Patricia tree, with no validation:

```cairo
let (prev_value) = get_contract_state_hash(
    class_hash=prev_state.class_hash,
    storage_root=initial_contract_state_root,
    nonce=prev_state.nonce,
);
``` [3](#0-2) 

This is the direct analog of the PRBProxy report's finding: important protocol state (`class_hash`, analogous to the `plugins`/`permissions` mappings) is stored without protection against being set to an invalid or malicious value by the contract itself. In the PRBProxy case, the fix was to move the state to a registry with enforced validation; here, the OS must enforce that the replacement class hash is declared before accepting the state transition.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is committed to the global state root as an undeclared value:

- Every subsequent call to the contract fails: the OS looks up the class hash in the compiled class facts, finds nothing, and cannot execute the entry point.
- No recovery syscall exists. The only way to "fix" the contract would be to declare a class whose hash equals the arbitrary felt — computationally infeasible for a random value.
- All ERC-20 balances, ETH, or other assets stored in the contract's storage are permanently inaccessible.

---

### Likelihood Explanation

**Medium.**

The attack is reachable by an unprivileged transaction sender via two realistic paths:

1. **Malicious freeze**: An attacker deploys a contract that accepts deposits and exposes a `freeze()` function calling `replace_class(0xdeadbeef)` where `0xdeadbeef` is not declared. After users deposit funds, the attacker calls `freeze()`. The OS accepts the transition; funds are frozen.

2. **Accidental upgrade race**: A legitimate contract's governance calls `replace_class` with a class hash that has not yet been declared in the same block (e.g., declaration and upgrade are in separate transactions that arrive out of order). The OS accepts the invalid transition; the contract is permanently broken.

No privileged role, leaked key, or external dependency is required. The entry point is a standard `invoke` transaction callable by any account.

---

### Recommendation

In `execute_replace_class`, before writing the new `class_hash` to `contract_state_changes`, verify that the class hash exists either in `contract_class_changes` (declared in the current block) or in the pre-existing contract class tree. This is precisely what the TODO at line 898 calls for and must be implemented before the function is considered production-safe. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that:
   - Accepts deposits (stores user balances in storage).
   - Exposes a public `freeze()` function that calls `replace_class(0x1)`, where `0x1` is not a declared class hash.

2. Users deposit funds into `MaliciousVault`.

3. Attacker sends an `invoke` transaction calling `freeze()`.

4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0x1` from the syscall segment (line 896).
   - Skips the missing validation (line 898 TODO).
   - Writes `StateEntry(class_hash=0x1, ...)` into `contract_state_changes` (lines 902–910).

5. `squash_state_changes` and `compute_contract_state_commitment` commit this entry to the global state root without objection. [5](#0-4) 

6. All future calls to `MaliciousVault` fail: the OS cannot locate class `0x1` in the compiled class facts.

7. All deposited funds are permanently frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-912)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/squash.cairo (L60-66)
```text
    assert [squashed_prev_state] = StateEntry(
        class_hash=prev_state.class_hash, storage_ptr=squashed_storage_ptr, nonce=prev_state.nonce
    );

    local squashed_new_state: StateEntry* = new StateEntry(
        class_hash=new_state.class_hash, storage_ptr=squashed_storage_ptr_end, nonce=new_state.nonce
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L190-194)
```text
    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L69-74)
```text
    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );
```
