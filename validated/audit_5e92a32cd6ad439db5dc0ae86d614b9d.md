### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract is actually declared in the system. Any contract can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts and commits this state change without enforcement, permanently rendering the contract uncallable and freezing any funds it holds.

---

### Finding Description

The vulnerability class from the external report is a **state-transition bypass**: a required lifecycle step is skipped, creating a permanent, irrecoverable state corruption. The direct analog here is `execute_replace_class` bypassing the mandatory "class must be declared before use" invariant.

In the StarkNet OS, the normal lifecycle for using a class is:

1. **Declare** the class via `execute_declare_transaction` → adds `class_hash → compiled_class_hash` to `contract_class_changes` with `prev_value=0` (enforcing uniqueness).
2. **Deploy or replace** to use the declared class.

The `execute_replace_class` function in `syscall_impls.cairo` skips step 1 entirely. It reads `request.class_hash` from the syscall request and writes it directly into `contract_state_changes` without any cross-check against `contract_class_changes`: [1](#0-0) 

The missing check is explicitly acknowledged by the developers themselves: [2](#0-1) 

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

By contrast, `execute_declare_transaction` correctly enforces that a class can only be declared once by using `prev_value=0`: [3](#0-2) 

And `deploy_contract` enforces that the target address is uninitialized before deployment: [4](#0-3) 

Neither of these protections applies to `replace_class`. The OS state update pipeline (`state_update` in `state.cairo`) squashes and commits whatever is in `contract_state_changes` without any post-hoc validation of class hash legitimacy: [5](#0-4) 

The compiled class facts validation (`validate_compiled_class_facts_post_execution`) only validates the facts provided as OS input, not whether every class hash referenced in the state diff is actually among those facts: [6](#0-5) 

---

### Impact Explanation

Once a contract's class hash is set to an undeclared value and the block is proven and committed to L1:

1. The contract's `StateEntry.class_hash` permanently points to a hash with no corresponding compiled class (CASM).
2. Any future transaction attempting to call the contract will fail to resolve the class during OS execution.
3. The sequencer cannot include such calls in provable blocks.
4. All funds (STRK, ERC-20 tokens, or any assets) held in the contract's storage are **permanently frozen** with no recovery path.

This matches the allowed impact: **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

- The `replace_class` syscall is callable by any contract on itself — no privileged role is required.
- An attacker deploys a contract (or exploits an existing one with a reentrancy or logic bug) and triggers `replace_class` with an arbitrary felt value as the class hash.
- The OS Cairo code performs zero validation of the new class hash against declared classes.
- The attack is a single transaction and is irreversible once the block is proven.

---

### Recommendation

In `execute_replace_class`, before updating `contract_state_changes`, verify that `class_hash` exists as a key in `contract_class_changes` with a non-zero value (i.e., it has been declared). This mirrors the invariant already enforced in `execute_declare_transaction` via `prev_value=0` and in `deploy_contract` via `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH`.

---

### Proof of Concept

1. Attacker deploys contract `C` using declared class `A`. Users deposit funds into `C`.
2. Attacker sends an invoke transaction that causes `C` to call the `replace_class` syscall with `class_hash = 0xDEADBEEF` (an arbitrary undeclared felt).
3. `execute_replace_class` in `syscall_impls.cairo` (line 896–910) writes `StateEntry(class_hash=0xDEADBEEF, ...)` into `contract_state_changes` with no validation.
4. The block is proven. `state_update` squashes and commits the change. L1 accepts the new state root.
5. Any subsequent transaction calling `C` fails: the OS cannot find a compiled class for `0xDEADBEEF` in the compiled class facts bundle.
6. All funds in `C` are permanently inaccessible — the contract is bricked with no upgrade or recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
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
