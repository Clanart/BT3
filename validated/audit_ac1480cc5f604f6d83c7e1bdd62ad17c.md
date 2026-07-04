### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The OS-level implementation of the `replace_class` syscall does not verify that the supplied class hash corresponds to a previously declared class. An attacker-controlled contract can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts this state transition, permanently setting the contract's class to a non-existent value. All funds held in that contract become permanently inaccessible.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS reads the new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in the declared-class tree (`contract_class_changes`):

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

The inline TODO at line 898 explicitly acknowledges the missing check. Contrast this with `execute_declare_transaction` (`transaction_impls.cairo`, lines 815–818), which enforces `prev_value=0` to guarantee a class is declared at most once and always with a non-zero compiled class hash:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

The class registry (`contract_class_changes`) is write-once and append-only — there is no removal or undeclare operation. Once a contract's class pointer is set to an undeclared hash via `replace_class`, the contract is permanently broken: it cannot execute (no class found), and therefore cannot call `replace_class` again to self-repair.

The revert log does record the old class hash:

```cairo
assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
```

However, this only helps if the enclosing transaction reverts. Because the OS accepts the syscall unconditionally, the transaction succeeds, the revert log entry is never applied, and the invalid class hash is committed to the global state.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 tokens, ETH (bridged STRK/ETH), or other assets held in a contract whose class is replaced with an undeclared hash are permanently inaccessible:

1. The contract's `StateEntry.class_hash` is set to an undeclared felt value.
2. Every subsequent call to the contract fails at class-lookup time.
3. The contract cannot execute its own logic, so it cannot call `replace_class` to recover.
4. There is no protocol-level mechanism to forcibly reset a contract's class hash.
5. The state commitment (`compute_contract_state_commitment`) faithfully commits this broken state into the global Merkle root, making it canonical and irreversible.

---

### Likelihood Explanation

**Medium.**

The attacker-controlled entry path requires one of:

- **Direct**: An attacker deploys a contract, attracts deposits (e.g., by impersonating a legitimate protocol), then calls `replace_class(arbitrary_undeclared_hash)` in a single transaction. The OS accepts it; funds are frozen.
- **Indirect**: Any contract that exposes a function allowing an external caller to influence the argument to `replace_class` (e.g., an upgradeable proxy with insufficient access control) can be exploited by an unprivileged transaction sender.

The syscall is reachable by any deployed contract. No privileged operator role is required. The missing check is confirmed by the TODO comment in the production OS code.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., its compiled class hash is non-zero). This mirrors the existing guard in `execute_declare_transaction`. Remove the TODO and add an assertion such as:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This ensures `replace_class` can only transition a contract to a live, declared class, closing the one-way invalid-state-transition window.

---

### Proof of Concept

1. Attacker deploys contract `C` with a valid class hash `H_valid`.
2. Users deposit funds into `C` (e.g., via a constructor or deposit function).
3. Attacker calls a function on `C` that executes `replace_class(H_undeclared)` where `H_undeclared` is any felt not present in the class tree.
4. The OS executes `execute_replace_class`:
   - Reads `H_undeclared` from the syscall request.
   - Skips the missing declared-class check (line 898 TODO).
   - Writes `StateEntry(class_hash=H_undeclared, ...)` into `contract_state_changes`.
   - Records the old class hash in the revert log.
5. Transaction succeeds; `compute_contract_state_commitment` commits the new state root containing `C → H_undeclared`.
6. All subsequent calls to `C` fail: the OS cannot find a compiled class for `H_undeclared`.
7. All funds in `C` are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L76-111)
```text
func compute_contract_state_commitment{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_state_changes_start: DictAccess*,
    n_contract_state_changes: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> CommitmentUpdate {
    alloc_locals;

    // Hash the entries of the contract state changes to prepare the input for the commitment tree
    // multi-update.
    let (local hashed_state_changes: DictAccess*) = alloc();
    compute_contract_state_commitment_inner(
        state_changes=contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        hashed_state_changes=hashed_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the contracts' state tree.
    local initial_root;
    local final_root;

    %{ SetPreimageForStateCommitments %}

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_state_changes,
        n_updates=n_contract_state_changes,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (CommitmentUpdate(initial_root=initial_root, final_root=final_root));
}
```
