### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary class hash from the calling contract without verifying that the hash corresponds to a declared class in the contract class tree. A malicious contract deployer can exploit this to permanently freeze a contract and all funds it holds by replacing its class with an undeclared hash.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts the caller-supplied `class_hash` and directly commits it to the contract state without any membership check against the declared class tree. The missing check is explicitly acknowledged in production code: [1](#0-0) 

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

The new `class_hash` is committed into the `contract_state_changes` dict and subsequently into the Patricia Merkle tree via `compute_contract_state_commitment`: [2](#0-1) 

The `get_contract_state_hash` function hashes the (potentially undeclared) `class_hash` into the leaf value: [3](#0-2) 

Once this state is committed and proven, the contract's on-chain class pointer is permanently set to a hash that has no corresponding compiled class. No valid OS proof can ever be generated for a future call to this contract, because the hint-driven class lookup (`guess_compiled_class_facts`) will find no entry for the undeclared hash, making proof generation impossible.

The class declaration path enforces `prev_value=0` to prevent re-declaration: [4](#0-3) 

But `execute_replace_class` performs no symmetric check — it does not verify that the target hash has `new_value != 0` in `contract_class_changes` or in the existing class tree.

---

### Impact Explanation

**Permanent freezing of funds.** Once a contract's `class_hash` is committed to the Patricia tree as an undeclared hash, the contract is irrecoverably frozen. No valid STARK proof can be generated for any transaction that calls the contract, because the OS cannot resolve the class. All assets (ERC-20 tokens, ETH, NFTs) held by the contract are permanently locked with no recovery path.

A malicious contract deployer can:
1. Deploy a contract that appears legitimate (escrow, DEX, vault, token bridge).
2. Attract user deposits.
3. Call `replace_class` with an arbitrary undeclared hash (e.g., `0xdead...`).
4. The OS commits the invalid class hash to the global state root without rejection.
5. All user funds are permanently frozen.

---

### Likelihood Explanation

- The attack requires only the ability to **deploy a contract** — an explicitly listed unprivileged entry point in the scope.
- The missing check is confirmed by a production TODO comment dated `1/1/2026`, meaning it is a known gap in the current codebase.
- No special privilege, leaked key, or operator access is required.
- The exploit is a single syscall invocation; no complex setup is needed beyond contract deployment.

---

### Recommendation

Before committing the new `class_hash` to `contract_state_changes`, verify that the hash corresponds to a declared class. Concretely, check that the hash exists in `contract_class_changes` with a non-zero `new_value`, or perform a read against the existing contract class Patricia tree to confirm the leaf is non-zero. Reject the syscall with an error if the class is undeclared, consistent with how `dict_update` enforces `prev_value=0` for new class declarations.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract with a `freeze()` function that calls `replace_class(0x000...DEAD)` where `0x000...DEAD` is never declared.
2. Users deposit funds into `MaliciousVault` (it behaves normally until `freeze()` is called).
3. Attacker calls `freeze()`.
4. The OS executes `execute_replace_class`:
   - `class_hash = 0x000...DEAD` (undeclared).
   - No existence check is performed (the TODO confirms this).
   - `dict_update` commits `class_hash=0x000...DEAD` into `contract_state_changes`.
5. `state_update` → `compute_contract_state_commitment` → `patricia_update` commits the invalid leaf to the global state root.
6. The STARK proof is generated and verified on L1 — the proof is valid because the OS Cairo constraints are satisfied (no constraint checks for class existence).
7. All future calls to `MaliciousVault` fail at proof-generation time: the OS hint system cannot resolve `0x000...DEAD` to any compiled class, making proof generation impossible.
8. User funds are permanently frozen with no recourse.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L51-71)
```text
func get_contract_state_hash{hash_ptr: HashBuiltin*}(
    class_hash: felt, storage_root: felt, nonce: felt
) -> (hash: felt) {
    const CONTRACT_STATE_HASH_VERSION = 0;
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }

    // Set res = H(H(class_hash, storage_root), nonce).
    let (hash_value) = hash2(class_hash, storage_root);
    let (hash_value) = hash2(hash_value, nonce);

    // Return H(hash_value, CONTRACT_STATE_HASH_VERSION). CONTRACT_STATE_HASH_VERSION must be in the
    // outermost hash to guarantee unique "decoding".
    let (hash) = hash2(hash_value, CONTRACT_STATE_HASH_VERSION);
    return (hash=hash);
}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
