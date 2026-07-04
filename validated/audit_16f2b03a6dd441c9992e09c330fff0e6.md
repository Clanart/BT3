### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by the caller is actually declared in the class tree (`contract_class_changes`). This is the direct analog of the reference vulnerability: just as removing a role from `_roles` without cleaning up `hasRole` leaves stale authorization state, replacing a contract's class hash without checking the class registry leaves the contract's state pointing to a non-existent class. Any contract can set its `class_hash` field to an arbitrary, undeclared felt value, permanently rendering itself uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function (lines 878–916) processes the `replace_class` syscall. After deducting gas, it reads the current `StateEntry` for the calling contract and writes a new `StateEntry` with the caller-supplied `class_hash` into `contract_state_changes`:

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
```

The TODO comment is the OS developers' own acknowledgment that the check is missing. There is **no assertion** that `class_hash` exists as a key in `contract_class_changes`. The two state structures — the contract state tree (`contract_state_changes`) and the class tree (`contract_class_changes`) — are updated independently, and `execute_replace_class` only touches the former.

Compare this with `execute_declare_transaction`, which enforces a strict `prev_value=0` constraint when writing to `contract_class_changes`, guaranteeing that only legitimately declared classes enter the class tree:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

No equivalent cross-check exists in `execute_replace_class` to confirm the target class hash is present in `contract_class_changes`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a contract's `class_hash` in `contract_state_changes` is set to a value that has no corresponding entry in the class tree, every subsequent call to that contract will fail at the class-lookup stage. The contract becomes permanently uncallable. Any ERC-20 balances, ETH/STRK, or other assets whose ownership or withdrawal logic is implemented in that contract's storage are irrecoverably frozen.

A concrete attack path:

1. Attacker deploys a contract that accepts user deposits (e.g., a vault or liquidity pool).
2. Users deposit funds; balances are recorded in the contract's storage.
3. Attacker invokes `replace_class` from within the contract, supplying an arbitrary felt (e.g., `0x1`, or any value never declared) as the new class hash.
4. The OS writes `class_hash = 0x1` into `contract_state_changes` without any validation.
5. All future calls to the contract attempt to execute entry points of class `0x1`, which does not exist in the class tree.
6. Every call reverts. Deposited funds are permanently frozen.

---

### Likelihood Explanation

**Medium.** The syscall is reachable by any unprivileged contract deployer. No special role, leaked key, or operator privilege is required. The attacker only needs to deploy a contract that (a) accepts deposits from other users and (b) calls `replace_class` with an undeclared hash. The TODO comment confirms the developers are aware the check is absent, meaning the window of exploitability is open until the fix is shipped.

---

### Recommendation

Before writing the new `StateEntry` into `contract_state_changes`, assert that the supplied `class_hash` is present in `contract_class_changes`. Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
// Verify the replacement class is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant already enforced by `execute_declare_transaction` and closes the inconsistency between the two state trees.

---

### Proof of Concept

**Root cause — missing cross-tree validation:** [1](#0-0) 

**Contrast — class declaration enforces existence in the class tree:** [2](#0-1) 

**State entry structure showing `class_hash` is the sole class identifier for a contract:** [3](#0-2) 

**Class tree commitment — only declared classes appear here; an undeclared hash has no leaf:** [4](#0-3) 

**Squash confirms the two trees are committed independently with no cross-validation:** [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L211-221)
```text
func get_contract_class_leaf_hash{poseidon_ptr: PoseidonBuiltin*}(compiled_class_hash: felt) -> (
    hash: felt
) {
    if (compiled_class_hash == UNINITIALIZED_CLASS_HASH) {
        return (hash=0);
    }

    // Return H(CONTRACT_CLASS_LEAF_VERSION, compiled_class_hash).
    let (hash_value) = poseidon_hash(CONTRACT_CLASS_LEAF_VERSION, compiled_class_hash);
    return (hash=hash_value);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L76-87)
```text
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
