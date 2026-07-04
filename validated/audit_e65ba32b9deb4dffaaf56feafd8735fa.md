### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary class hash from a contract's syscall request without verifying that the hash corresponds to a declared contract class. This allows any contract to permanently corrupt its own class hash entry in the global state, making the contract permanently uncallable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` (lines 878–916) reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` via `dict_update` with no validation:

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

The TODO comment at line 898 explicitly acknowledges the missing check. There is no Cairo constraint anywhere in the OS that verifies `class_hash` exists in `contract_class_changes` (the declared class registry) before committing the state update.

The analog to the external report is precise: just as `syncCash()` in the lending pool reads an external token balance (`balanceOf`) and writes it into internal accounting without validating against admin-configured restrictions, `execute_replace_class` reads an external class hash from the contract's syscall request and writes it into the global state without validating against the declared-class registry. Both bypass an implicit protocol-level restriction (admin-configured collateral allowlist vs. declared-class-only invariant) by accepting unvalidated external data into internal state.

The `execute_replace_class` syscall is reachable by any executing contract via the syscall dispatch in `execute_syscalls`:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
``` [2](#0-1) 

After the state is committed with an undeclared class hash, any future transaction targeting that contract will fail at proof generation: the prover cannot supply a valid `CompiledClassFact` for the undeclared hash, and `validate_compiled_class_facts_post_execution` will reject the block. The contract becomes permanently uncallable. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the block is finalized, no valid proof can ever be generated for a transaction that calls that contract. All ERC-20 balances, NFTs, or protocol reserves held by the contract are permanently inaccessible. There is no recovery path because the state root is committed on-chain.

---

### Likelihood Explanation

The entry point is the **contract deployer**, an explicitly allowed unprivileged role. The attack requires no leaked keys, no privileged operator access, and no third-party compromise. Any contract can issue the `replace_class` syscall. A malicious deployer can:

1. Deploy a contract that accepts user deposits and exposes a `freeze()` function.
2. Attract deposits from users.
3. Call `freeze()`, which internally calls `replace_class(arbitrary_undeclared_hash)`.
4. The OS commits the state with the invalid class hash.

The attack is a single transaction and is irreversible.

---

### Recommendation

Inside `execute_replace_class`, before writing `new_state_entry`, add a Cairo-level check that `class_hash` exists in `contract_class_changes` with a non-zero compiled class hash. Specifically, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero. This mirrors how `execute_declare_transaction` enforces `prev_value=0` to prevent double-declaration:

```cairo
// Enforce that the target class is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
``` [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys contract `MaliciousVault` with:
   - `deposit()` — accepts user funds.
   - `freeze()` — calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.

2. Users deposit funds into `MaliciousVault`.

3. Attacker submits a transaction calling `freeze()`.

4. The OS dispatches `REPLACE_CLASS_SELECTOR` → `execute_replace_class`.

5. `execute_replace_class` reads `class_hash = 0xdeadbeef` from the request and writes:
   ```
   dict_update(contract_state_changes,
     key=MaliciousVault_address,
     prev_value=<old_state_entry>,
     new_value=StateEntry(class_hash=0xdeadbeef, ...))
   ```
   No validation occurs. [5](#0-4) 

6. The block is finalized. The global state root now encodes `MaliciousVault → class_hash=0xdeadbeef`. [6](#0-5) 

7. Any future transaction calling `MaliciousVault` requires the prover to supply a `CompiledClassFact` for `0xdeadbeef`. No such fact exists. Proof generation fails. The contract is permanently uncallable. All deposited funds are frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-197)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
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
