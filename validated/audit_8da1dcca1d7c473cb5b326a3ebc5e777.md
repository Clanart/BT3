### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program updates a contract's class hash in `contract_state_changes` without verifying that the supplied class hash is actually declared in `contract_class_changes`. This creates an inconsistency between the two mappings — analogous to the reported `tokenByName` bug where a mapping is updated without cleaning up or validating the counterpart mapping — and allows any contract deployer to permanently brick a contract, freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` performs the following steps:

1. Reads the new `class_hash` from the syscall request.
2. Updates `contract_state_changes[contract_address].class_hash` to the new value.
3. Logs the old class hash to the revert log. [1](#0-0) 

Critically, step 2 is performed **without any check** that the new `class_hash` exists as a key in `contract_class_changes` (the class registry). The code itself acknowledges this with an explicit TODO: [2](#0-1) 

The same missing check exists in the deprecated path: [3](#0-2) 

The two mappings that become inconsistent are:

- `contract_state_changes`: `contract_address → StateEntry { class_hash, storage_ptr, nonce }` — updated by `replace_class`.
- `contract_class_changes`: `class_hash → compiled_class_hash` — **not consulted or validated** during `replace_class`. [4](#0-3) 

This is the direct analog to the reported `tokenByName` bug: just as `tokenByName["Bob"]` still points to Bob's NFT after "Alice" is transferred to it (because the old entry was never cleared/validated), here `contract_state_changes[addr].class_hash` can point to a class hash that has no entry in `contract_class_changes` (because the new value was never validated against the class registry).

---

### Impact Explanation

After `replace_class` sets a contract's class hash to an undeclared value:

- Any subsequent call to that contract requires the OS to resolve the class hash to a compiled class via `contract_class_changes`. Since the hash is absent from that mapping, execution cannot proceed.
- The contract cannot self-recover: it cannot call `replace_class` again because it cannot execute at all.
- All funds (ERC-20 balances, NFTs, or any assets) held in the contract's storage are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

- The entry path requires only deploying a contract and invoking a transaction that calls `replace_class` with an arbitrary undeclared felt value as the class hash.
- No privileged role, leaked key, or external dependency is required.
- The missing check is explicitly flagged in the production code with a `TODO`, confirming it is a known gap in enforcement.
- Any unprivileged contract deployer or contract caller can trigger this.

---

### Recommendation

Before updating `contract_state_changes` in `execute_replace_class`, verify that the supplied `class_hash` exists in `contract_class_changes` (i.e., has a non-zero `compiled_class_hash` entry). This mirrors how `execute_declare_transaction` enforces `prev_value=0` to prevent double-declaration: [5](#0-4) 

A `dict_read` on `contract_class_changes` keyed by the new `class_hash` should be performed, and the syscall should fail (write a failure response) if the result is zero (undeclared). This must be applied to both `execute_replace_class` implementations.

---

### Proof of Concept

1. Attacker deploys contract `C` with valid class hash `H_valid` (declared, so `contract_class_changes[H_valid] = compiled_hash`).
2. Contract `C` contains logic to call `replace_class(H_fake)` where `H_fake` is an arbitrary felt not present in `contract_class_changes`.
3. Attacker sends funds (e.g., ERC-20 tokens) to contract `C`.
4. Attacker invokes a transaction that triggers `C`'s `replace_class(H_fake)` call.
5. The OS executes `execute_replace_class`: sets `contract_state_changes[C].class_hash = H_fake` with no validation against `contract_class_changes`.
6. The block is proven and state is committed: `C`'s class hash is now `H_fake` on-chain.
7. Any future call to `C` requires resolving `H_fake` in `contract_class_changes` — this lookup yields zero (undeclared), execution fails unconditionally.
8. Funds in `C` are permanently frozen. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/squash.cairo (L84-103)
```text
// Takes a dict of the class changes and produces a squashed dict.
func squash_class_changes{range_check_ptr}(
    class_changes_start: DictAccess*, class_changes_end: DictAccess*
) -> (n_class_updates: felt, squashed_contract_state_dict: DictAccess*) {
    alloc_locals;

    local squashed_dict: DictAccess*;
    %{ GuessClassesPtr %}
    let (local squashed_dict_end) = squash_dict(
        dict_accesses=class_changes_start,
        dict_accesses_end=class_changes_end,
        squashed_dict=squashed_dict,
    );

    %{ UpdateClassesPtr %}

    return (
        n_class_updates=(squashed_dict_end - squashed_dict) / DictAccess.SIZE,
        squashed_contract_state_dict=squashed_dict,
    );
```
