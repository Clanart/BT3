### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS updates `contract_state_changes` with an attacker-supplied class hash without verifying that the hash exists in `contract_class_changes` (the declared-class registry). This state-synchronization gap — directly analogous to the external report's pattern of updating one component while leaving a dependent component stale — allows any contract to replace its own class with an undeclared, non-existent hash. Once committed, the contract becomes permanently uncallable, freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no cross-check against `contract_class_changes`:

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

The TODO comment is the OS developers' own acknowledgement that the cross-component consistency check is absent. Compare this with how `execute_declare_transaction` in `transaction_impls.cairo` correctly writes the class into `contract_class_changes`:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

`execute_replace_class` writes only to `contract_state_changes` and never consults `contract_class_changes`. The two dictionaries are squashed and committed independently in `state_update` in `state/state.cairo`: [3](#0-2) 

After squashing, `compute_contract_state_commitment` hashes the new `StateEntry` (which contains the attacker-chosen class hash) into the contract state Merkle tree, and `compute_class_commitment` hashes only the legitimately declared classes into the class tree. The two trees are then combined into the global state root via `calculate_global_state_root`. The invalid class hash is permanently committed to the state root with no rejection path. [4](#0-3) 

---

### Impact Explanation

Once the block is proven and the state root is updated on L1, the contract's on-chain class hash is an undeclared value. Every subsequent call to the contract will fail at class-lookup time because the hash does not appear in the compiled class facts bundle. The contract is permanently bricked. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are irrecoverable — a direct match for the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The entry path requires only an unprivileged contract call. Any deployed contract can issue the `replace_class` syscall with an arbitrary felt value as the class hash. A malicious contract can be purpose-built to self-destruct in this way after receiving a deposit (e.g., a honeypot), or a legitimate contract with a bug in its upgrade logic could trigger this accidentally. No privileged role, leaked key, or external dependency is required. The OS itself is the only validator that matters for proof soundness, and it performs no check.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, verify that `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it was declared in the current block) **or** that it already exists in the global class tree (i.e., it was declared in a prior block). The analogous pattern already exists for `deploy_contract`, which asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing. A symmetric assertion — that the replacement class hash resolves to a known compiled class — should be enforced here. [5](#0-4) 

---

### Proof of Concept

1. Deploy contract `C` that holds user funds.
2. From within `C`, issue `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS executes `execute_replace_class`: gas is deducted, `contract_state_changes` is updated with `class_hash = 0xdeadbeef`, no check against `contract_class_changes` is performed.
4. `state_update` squashes both dictionaries. `compute_contract_state_commitment` commits `0xdeadbeef` into the contract state tree. `compute_class_commitment` has no entry for `0xdeadbeef`.
5. The global state root is updated and proven on L1.
6. Any future call to `C` fails: the OS cannot find compiled class facts for `0xdeadbeef`.
7. All funds in `C`'s storage are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L70-87)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L38-49)
```text
func calculate_global_state_root{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    contract_state_root: felt, contract_class_root: felt
) -> (global_root: felt) {
    if (contract_state_root == 0 and contract_class_root == 0) {
        // The state is empty.
        return (global_root=0);
    }

    tempvar elements: felt* = new (GLOBAL_STATE_VERSION, contract_state_root, contract_class_root);
    let (global_root) = poseidon_hash_many(n=3, elements=elements);
    return (global_root=global_root);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
