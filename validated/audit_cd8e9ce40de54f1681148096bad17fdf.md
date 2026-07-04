### Title
Missing Class Hash Declaration Validation in `execute_replace_class` Enables Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program accepts an arbitrary class hash from a contract without verifying that the hash corresponds to a declared class. This allows a contract to permanently replace its own class with an undeclared, non-executable class hash, bricking the contract and permanently freezing any funds it holds. The missing check is explicitly acknowledged by a past-due TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` (lines 878–916) handles the `replace_class` syscall. It reads `request.class_hash` from the syscall buffer and directly writes it into the contract's `StateEntry` in `contract_state_changes` — with no check that the class hash has ever been declared.

The critical missing validation is explicitly flagged by a TODO comment at line 898:

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

The TODO deadline of `1/1/2026` has already passed (today is 2026-07-03), yet the check remains absent.

The `contract_class_changes` dictionary — which maps `class_hash → compiled_class_hash` and is the authoritative record of declared classes — is only updated during `execute_declare_transaction`:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

`execute_replace_class` does **not** touch `contract_class_changes`. It only updates `contract_state_changes`. After a successful `replace_class(undeclared_hash)` call, the contract's leaf in the contract state Patricia tree records `class_hash = undeclared_hash`, while the class tree has no entry for `undeclared_hash` (its compiled class hash is 0 / `UNINITIALIZED_CLASS_HASH`). [3](#0-2) 

Post-execution, the OS validates all compiled class facts used during execution via `validate_compiled_class_facts_post_execution`:

```cairo
validate_compiled_class_facts_post_execution(
    n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
    compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
    builtin_costs=compiled_class_facts_bundle.builtin_costs,
);
``` [4](#0-3) 

Any future call to the bricked contract would require the prover to supply a compiled class for `undeclared_hash`. The post-execution validation would compare the supplied compiled class hash against the class tree entry (which is 0), causing the proof to be invalid. No valid proof can ever be generated for a block containing a call to this contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is committed to the Patricia tree as an undeclared value:

1. The contract's `StateEntry.class_hash` in the contract state tree is `undeclared_hash`.
2. The class tree has no leaf for `undeclared_hash` (compiled class hash = 0).
3. Any transaction calling the contract requires the prover to provide a compiled class for `undeclared_hash`.
4. `validate_compiled_class_facts_post_execution` rejects any compiled class whose hash does not match the class tree entry (0).
5. No valid STARK proof can be produced for any block that calls the contract.
6. The contract is permanently non-callable; all funds it holds are permanently frozen.

The state squashing and commitment logic confirms that the contract state tree and class tree are committed independently: [5](#0-4) 

There is no recovery path: the contract cannot call `replace_class` again (it is non-callable), and there is no admin override in the OS.

---

### Likelihood Explanation

The attack is reachable by any unprivileged transaction sender who can invoke a contract function that calls `replace_class`. Realistic vectors include:

- **Upgradeable contracts with public or weakly-guarded upgrade functions** that accept a caller-supplied class hash. Since no syscall exists to check whether a class hash is declared, the contract itself cannot guard against this.
- **Reentrancy attacks** that manipulate the class hash mid-execution before the transaction completes.
- **Buggy contracts** that pass an unvalidated user input to `replace_class`.

Because the OS provides no `is_class_declared` syscall, contracts have no on-chain mechanism to validate the class hash before calling `replace_class`. The OS is the only enforcement point, and it currently enforces nothing.

---

### Recommendation

Inside `execute_replace_class`, before updating `contract_state_changes`, verify that `class_hash` exists in `contract_class_changes` (i.e., its compiled class hash is non-zero). Concretely:

```cairo
// Read the compiled class hash for the requested class hash.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
// Reject if the class has not been declared.
assert_not_zero(compiled_class_hash);
```

This mirrors the pattern already used in `execute_declare_transaction`, which enforces `prev_value=0` to prevent double-declaration, and ensures the OS is the authoritative validator of class existence — not the sequencer alone.

---

### Proof of Concept

1. Deploy **Contract A** (holding funds) with an `upgrade(new_class_hash: felt)` function that calls `replace_class(new_class_hash)` with no access control or validation.
2. An unprivileged attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. `execute_replace_class` runs; the TODO check is absent; `contract_state_changes` is updated: `Contract A → StateEntry(class_hash=0xdeadbeef, ...)`.
4. The transaction succeeds. The state is committed. The Patricia tree now records `class_hash=0xdeadbeef` for Contract A.
5. In the next block, any transaction calling Contract A requires the prover to supply a compiled class for `0xdeadbeef`.
6. `validate_compiled_class_facts_post_execution` compares the supplied compiled class hash against the class tree entry for `0xdeadbeef`, which is 0 (`UNINITIALIZED_CLASS_HASH`). Mismatch → proof invalid.
7. No valid proof can be generated for any block calling Contract A. Contract A is permanently non-callable. All funds are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-912)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L57-87)
```text
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
```
