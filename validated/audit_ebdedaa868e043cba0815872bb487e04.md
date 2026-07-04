### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS Cairo program accepts any arbitrary class hash as the replacement class without verifying that the hash corresponds to a previously declared class. An unprivileged contract deployer can exploit this to permanently freeze any funds held by a contract they control by replacing its class with an undeclared hash, rendering the contract permanently uncallable.

---

### Finding Description

The vulnerability class from the external report is **state-transition bypass via missing guard on a finalization-like operation**: a state change that should be gated by a validity check proceeds without it. The analog here is `execute_replace_class`, which performs a permanent, irreversible state mutation (changing a contract's class hash) without verifying the new class hash is declared.

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no existence check:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The `contract_class_changes` dict (which tracks declared classes) is not consulted at all during `execute_replace_class`.

For contrast, the `execute_declare_transaction` function correctly enforces `prev_value=0` to prevent re-declaration and validates the class hash pre-image: [2](#0-1) 

No equivalent guard exists in `execute_replace_class`.

The state commitment pipeline in `commitment.cairo` then faithfully commits whatever class hash is stored in `StateEntry` into the global Merkle root, with no post-hoc validation: [3](#0-2) 

Once committed, the invalid class hash is part of the canonical on-chain state.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class` is called with an undeclared hash:

1. The contract's `StateEntry.class_hash` is updated to the arbitrary value and committed to the global state root.
2. Any future invocation of the contract causes the OS to attempt to look up the class in the compiled class facts bundle. Since the hash was never declared, no matching entry exists.
3. All entry points of the contract become permanently unreachable.
4. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path, because the contract can never execute again.

The state change is irreversible: there is no mechanism in the protocol to "un-replace" a class hash once committed.

---

### Likelihood Explanation

**Medium-High.** The attack requires only:

1. Deploying a contract (permissionless on StarkNet).
2. Calling `replace_class` from within that contract with an arbitrary felt value as the class hash.

No privileged access, leaked keys, or operator cooperation is needed. The attacker controls the contract code and can craft a constructor or any entry point to call `replace_class` with a chosen invalid hash. The attack is deterministic and requires a single transaction.

A realistic scenario: a malicious contract factory deploys victim contracts and immediately calls `replace_class` with a garbage hash before transferring ownership, permanently freezing deposited funds.

---

### Recommendation

In `execute_replace_class`, before updating `contract_state_changes`, verify that `class_hash` exists as a key in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely:

- Perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero.
- Alternatively, maintain a separate set of declared class hashes and assert membership before accepting the replacement.

This mirrors the guard already present in `execute_declare_transaction` (`prev_value=0` enforces uniqueness) and closes the analogous gap in `execute_replace_class`.

---

### Proof of Concept

1. Deploy contract `VictimVault` that holds user funds and has an entry point `self_destruct()` that calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared hash).
2. A user deposits funds into `VictimVault`.
3. Attacker (or the contract itself on deploy) calls `self_destruct()`.
4. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No check against `contract_class_changes` is performed.
   - `contract_state_changes` is updated: `VictimVault.class_hash = 0xdeadbeef`.
5. `state_update` in `state.cairo` commits this into the global Merkle root.
6. In any subsequent block, any call to `VictimVault` causes the OS to look up class `0xdeadbeef` in the compiled class facts bundle — it is absent — and execution fails unconditionally.
7. All funds in `VictimVault` are permanently frozen. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L196-203)
```text
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L66-87)
```text
    // State is finalized.
    %{ ComputeCommitmentsOnFinalizedStateWithAliases %}

    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );
```
