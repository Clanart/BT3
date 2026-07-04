### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash has been declared in the protocol. This allows any contract to replace its class with an arbitrary, undeclared hash, rendering the contract permanently inoperable and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts any class hash value from the calling contract without checking whether it corresponds to a declared class in `contract_class_changes`: [1](#0-0) 

The critical section is:

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
``` [2](#0-1) 

After `replace_class` is called with an undeclared hash, the contract's `class_hash` field in `contract_state_changes` is set to a value that has no corresponding entry in the class tree. The state is then committed to the Patricia tree via `compute_contract_state_commitment`: [3](#0-2) 

Once committed, any subsequent call to the contract will attempt to look up the class bytecode using the undeclared hash. Since no class with that hash exists in the class tree, the call fails unconditionally. There is no recovery mechanism — the contract is permanently frozen.

**Analogy to the reported vulnerability:** In the reported bug, `completeValidatorRemoval()` deletes `_registeredValidators[nodeID]`, making `valID` unrecoverable, so `nodePendingRemoval[valID]` can never be cleared and the node stays in `operatorNodesArray` forever. Here, the OS accepts an arbitrary class hash (the "stale reference") without validating it against the declared class tree (the "deleted mapping"), leaving the contract in an irrecoverable state with no cleanup path.

The revert log correctly records the old class hash for potential revert: [4](#0-3) 

But if the transaction does **not** revert, the invalid class hash is permanently committed. The revert mechanism in `revert_contract_changes()` only helps within the same transaction: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that calls `replace_class` with an undeclared class hash becomes permanently inoperable. All funds (tokens, NFTs, ERC-20 balances, etc.) held by the contract are permanently frozen with no recovery path. The state is committed to the global Patricia tree and cannot be undone after the block is finalized.

---

### Likelihood Explanation

This is directly reachable by an unprivileged **contract deployer**:

1. Attacker deploys a malicious vault contract (class A) containing:
   - A public `deposit()` function that accepts user funds
   - A backdoor `freeze()` function that calls `replace_class(undeclared_hash)` where `undeclared_hash` is any felt value not present in the class tree
2. Users deposit funds into the vault (trusting the contract's declared class)
3. Attacker calls `freeze()` — the OS executes `replace_class` without any validation
4. The vault's `class_hash` in `contract_state_changes` is set to `undeclared_hash`
5. All future calls to the vault fail; user funds are permanently frozen

No privileged access, leaked keys, or external dependencies are required. The attack requires only the ability to deploy a contract and submit transactions — capabilities available to any unprivileged user.

---

### Recommendation

Add a validation step inside `execute_replace_class` to confirm that `class_hash` is present in `contract_class_changes` (i.e., was declared in the current block) or exists in the committed class tree before updating `contract_state_changes`. Concretely, perform a `dict_read` on `contract_class_changes` for the given `class_hash` and assert the result is non-zero, analogous to how `execute_declare_transaction` enforces `prev_value=0` to prevent double-declaration: [6](#0-5) 

---

### Proof of Concept

```cairo
// Malicious vault contract (pseudocode in Cairo-like syntax)
@external
func deposit{...}(amount: felt) {
    // Accept user funds (e.g., transfer ERC-20 tokens to this contract)
    IERC20.transfer_from(caller, self, amount);
}

@external
func freeze{syscall_ptr: felt*, ...}() {
    // Call replace_class with an arbitrary undeclared hash.
    // The OS accepts this without validation.
    replace_class(class_hash=0xDEADBEEF);  // 0xDEADBEEF is not declared
}
```

**Attack sequence:**
1. Attacker deploys the malicious vault
2. Users call `deposit()` — funds accumulate in the contract
3. Attacker calls `freeze()` — `execute_replace_class` in the OS sets `class_hash = 0xDEADBEEF` with no validation
4. `contract_state_changes` now holds `StateEntry(class_hash=0xDEADBEEF, ...)` for the vault address
5. This is committed to the Patricia tree via `compute_contract_state_commitment`
6. Any future call to the vault fails — class `0xDEADBEEF` does not exist in the class tree
7. All deposited funds are permanently frozen

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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

    return ();
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L87-91)
```text
    if (selector == CHANGE_CLASS_ENTRY) {
        // Change class entry.
        let class_hash = revert_log_end[0].value;
        return revert_contract_changes();
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
