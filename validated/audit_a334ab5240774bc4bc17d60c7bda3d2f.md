### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt value as a new class hash and commits it to state without verifying that the hash corresponds to a previously declared class. This is the direct protocol-level analog of the "lack of transferOwnership validation" pattern: a single-step privileged state mutation (class replacement) with no validation of the new value. Any contract that calls `replace_class` with an undeclared hash becomes permanently inaccessible, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` handles the `replace_class` syscall:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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

The `class_hash` value is taken directly from `request.class_hash` — a caller-controlled field — and written into `contract_state_changes` with no check that the hash exists in `contract_class_changes` (the declared class registry). The TODO comment at line 898 explicitly acknowledges this missing check. [1](#0-0) 

The `contract_class_changes` dict, which maps class hash → compiled class hash, is the authoritative registry of declared classes. It is populated only via `execute_declare_transaction` (which enforces `prev_value=0` and validates the Sierra class hash pre-image). The `execute_replace_class` handler has access to neither `contract_class_changes` nor any lookup into it. [2](#0-1) 

After `state_update` commits the squashed `contract_state_changes` to the global Patricia tree, the contract's on-chain class hash is permanently set to the undeclared value. [3](#0-2) 

Any future transaction targeting this contract will attempt to resolve the class hash against the compiled class facts bundle. Since no compiled class exists for the undeclared hash, execution cannot proceed and the contract is permanently bricked.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is finalized, the state root commits this change irreversibly. No subsequent transaction can execute code in the affected contract (no entry point can be dispatched), so any ERC-20 balances, ETH, or other assets held by the contract are permanently inaccessible. The freezing is not recoverable through any protocol mechanism because `replace_class` itself requires executing the contract's code, which is now impossible.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract during its own execution — no privileged role is required. The attacker-controlled entry path is:

1. An unprivileged user deploys a contract (e.g., a shared vault, an AMM pool, or a wallet) that contains logic invoking `replace_class`.
2. The contract calls `replace_class` with an arbitrary felt (e.g., `1`, or any hash not present in `contract_class_changes`).
3. `execute_replace_class` accepts the call, deducts gas, and writes the new `StateEntry` with the invalid class hash into `contract_state_changes`.
4. `state_update` squashes and commits the change to the global state root.
5. The contract is permanently frozen.

This requires no privileged access, no leaked key, and no operator cooperation. Any contract author — or any contract that can be induced to call `replace_class` — can trigger this. [4](#0-3) 

---

### Recommendation

Inside `execute_replace_class`, after reading `class_hash` from the request, perform a lookup into `contract_class_changes` to confirm the hash has a non-zero compiled class hash entry. Concretely, add `contract_class_changes` as an implicit argument and assert:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already enforced in `execute_declare_transaction` via `prev_value=0` and `assert_not_zero(compiled_class_hash)`, and closes the single-step unvalidated state-mutation gap that is the root cause of this finding. [5](#0-4) 

---

### Proof of Concept

1. **Deploy** a contract `VaultWithBug` that holds user funds and exposes an entry point `self_destruct()` which calls `replace_class(0xdeadbeef)` — an undeclared class hash.
2. **Invoke** `VaultWithBug.__execute__` → `self_destruct()`.
3. The OS calls `execute_replace_class`. At line 896, `class_hash = 0xdeadbeef`. At line 898, the TODO check is absent. `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. `state_update` in `state.cairo` squashes and commits the change; the global state root now encodes `VaultWithBug.class_hash = 0xdeadbeef`.
5. Any subsequent call to `VaultWithBug` fails at class resolution — no compiled class exists for `0xdeadbeef` in the compiled class facts bundle validated by `validate_compiled_class_facts_post_execution`.
6. All funds in `VaultWithBug` are permanently frozen. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L48-113)
```text
func state_update{poseidon_ptr: PoseidonBuiltin*, hash_ptr: HashBuiltin*, range_check_ptr}(
    os_state_update: OsStateUpdate, should_allocate_aliases: felt
) -> (squashed_os_state_update: SquashedOsStateUpdate*, state_update_output: CommitmentUpdate*) {
    alloc_locals;

    // Create PatriciaUpdateConstants struct for patricia update.
    let (local patricia_update_constants: PatriciaUpdateConstants*) = patricia_update_constants_new(
        );

    // (Maybe) allocate aliases and squash the final contract state tree.
    let (
        n_contract_state_changes, squashed_contract_state_changes_start
    ) = squash_state_changes_and_maybe_allocate_aliases(
        contract_state_changes_start=os_state_update.contract_state_changes_start,
        contract_state_changes_end=os_state_update.contract_state_changes_end,
        should_allocate_aliases=should_allocate_aliases,
    );

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

    // Compute the initial and final roots of the global state.
    let (local initial_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.initial_root,
        contract_class_root=contract_class_tree_update_output.initial_root,
    );
    let (local final_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.final_root,
        contract_class_root=contract_class_tree_update_output.final_root,
    );

    // Prepare the return values.
    tempvar squashed_os_state_update = new SquashedOsStateUpdate(
        contract_state_changes=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        contract_class_changes=squashed_class_changes,
        n_class_updates=n_class_updates,
    );

    tempvar state_update_output = new CommitmentUpdate(
        initial_root=initial_global_root, final_root=final_global_root
    );

    return (
        squashed_os_state_update=squashed_os_state_update, state_update_output=state_update_output
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```
