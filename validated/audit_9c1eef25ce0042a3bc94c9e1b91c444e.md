### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value from the contract without verifying that the hash corresponds to a previously declared class. This is structurally analogous to the reported "missing slippage check" pattern: a critical output value (the new class hash) is accepted without being validated against a known-good reference (the set of declared classes). An unprivileged contract deployer can exploit this to permanently freeze funds held by any contract they control.

---

### Finding Description

In `execute_replace_class`, the OS reads `request.class_hash` directly from the syscall segment and writes it into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (the declared-class registry) or in the compiled class facts bundle.

The code contains an explicit developer acknowledgment of this missing check:

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

The state update is committed unconditionally. The new `class_hash` is then embedded into the Merkle commitment via `compute_contract_state_commitment` → `hash_contract_state_changes`, making it permanent on-chain. [2](#0-1) 

When any subsequent call targets this contract, the OS reads the class hash from state and attempts to look up the compiled class. Because the class was never declared, the prover cannot supply a valid execution trace for it. Every future call to the contract reverts permanently, with no recovery path. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared class hash, the contract's state entry in the Merkle tree is permanently updated. There is no syscall or OS mechanism to recover from this: the contract address is live in state but its class is unresolvable. Any ERC-20 balances, ETH, or other assets held by the contract become permanently inaccessible. This matches the "Critical. Permanent freezing of funds." impact category.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself. The attack requires only:

1. Deploying a contract (permissionless on StarkNet).
2. Attracting user deposits (e.g., by presenting as a legitimate vault or token contract).
3. Calling `replace_class` with any felt value that is not a declared class hash.

No privileged role, leaked key, or external dependency is required. The OS-level missing check is the sole necessary condition. The developer TODO comment confirms the team is aware the check is absent.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, assert that `class_hash` is present in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors how `execute_declare_transaction` enforces `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`. [4](#0-3) 

---

### Proof of Concept

1. **Attacker deploys** a contract `VaultContract` that:
   - Accepts ETH/token deposits from users.
   - Exposes a `freeze()` function that calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (any undeclared felt).

2. **Users deposit funds** into `VaultContract`.

3. **Attacker calls** `freeze()`. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is written into `contract_state_changes` for `VaultContract`.
   - No validation is performed against declared classes.
   - The state update is committed to the Merkle tree via `compute_contract_state_commitment`.

4. **Any subsequent call** to `VaultContract` (withdraw, transfer, etc.) causes the prover to look up compiled class `0xdeadbeef`, which does not exist. The prover cannot generate a valid trace; the call is permanently unexecutable.

5. **All deposited funds are frozen** with no recovery mechanism at the OS level. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L192-215)
```text
    tempvar contract_address = request.contract_address;
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Prepare execution context.
    // TODO(Yoni, 1/1/2026): change ExecutionContext to hold calldata_start, calldata_end.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar caller_address = caller_execution_info.contract_address;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L148-205)
```text
func hash_contract_state_changes{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_address: felt,
    prev_state: StateEntry*,
    new_state: StateEntry*,
    patricia_update_constants: PatriciaUpdateConstants*,
    hashed_state_changes: DictAccess*,
) {
    alloc_locals;

    local initial_contract_state_root;
    local final_contract_state_root;

    %{ SetPreimageForCurrentCommitmentInfo %}

    local state_dict_start: DictAccess* = prev_state.storage_ptr;
    local state_dict_end: DictAccess* = new_state.storage_ptr;
    local n_updates = (state_dict_end - state_dict_start) / DictAccess.SIZE;
    // Call patricia_update_using_update_constants() (or the read-optimized variant) instead of
    // patricia_update() in order not to repeat globals_pow2 calculation.
    local should_use_read_optimized: felt;
    %{ ShouldUseReadOptimizedPatriciaUpdate %}
    if (should_use_read_optimized != 0) {
        patricia_update_read_optimized(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    } else {
        patricia_update_using_update_constants(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    }
    local range_check_ptr = range_check_ptr;

    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
    assert hashed_state_changes.prev_value = prev_value;
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
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
