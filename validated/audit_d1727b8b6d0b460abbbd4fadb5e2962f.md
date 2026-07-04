### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program accepts any caller-supplied class hash and writes it directly into the `contract_state_changes` mapping without verifying that the class hash has been declared (i.e., exists in `contract_class_changes`). This is structurally identical to the M-08 pattern: a mapping is updated with a reference to an entity whose validity is never confirmed, leaving the contract in a permanently broken state. Any contract holding funds that calls `replace_class` with an undeclared class hash will be permanently frozen, with no recovery path.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads the caller-supplied `class_hash` from the syscall request and immediately writes it into the contract's `StateEntry` in `contract_state_changes`:

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

The `TODO` comment at line 898 explicitly acknowledges the missing check. The function never consults `contract_class_changes` to confirm the new class hash was declared. Compare this to `execute_declare_transaction`, which enforces `prev_value=0` to guarantee a class is only written once it is properly declared:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

No equivalent guard exists in `execute_replace_class`.

After the syscall completes, the contract's `StateEntry.class_hash` in `contract_state_changes` is set to an arbitrary, undeclared value. The OS then commits this into the global state root via `squash_state_changes` → `compute_contract_state_commitment` → `hash_contract_state_changes`, none of which cross-check whether the class hash in a `StateEntry` actually exists in `contract_class_changes`. The proof is generated for a state where the contract's class hash points to a non-existent class.

Any subsequent call to this contract — via `call_contract`, `invoke`, or `execute_get_class_hash_at` — reads the class hash from `contract_state_changes` and attempts to dispatch to an entry point of the non-existent class. Execution fails unconditionally. There is no mechanism to recover: `replace_class` can only be called from within the contract's own execution context, which is now permanently unreachable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds token balances, acts as a vault, or is an account contract (wallet) can be permanently frozen. Once the contract's class hash is replaced with an undeclared hash and the block is proven, the state is finalized on-chain. The contract can never execute again. All funds held by the contract are irrecoverably locked.

---

### Likelihood Explanation

The attack surface is broad:

1. **Self-inflicted by a buggy contract:** A contract with a bug in its upgrade logic could call `replace_class` with an incorrect hash, permanently freezing itself and all funds it holds.
2. **Deliberate griefing:** A malicious contract author can deploy a contract, attract deposits (e.g., by mimicking a legitimate protocol), then call `replace_class(arbitrary_undeclared_hash)` to freeze all deposited funds.
3. **No privileged access required:** `replace_class` is a standard syscall callable by any contract on itself. No operator or admin role is needed.

The explicit `TODO` comment in the production code confirms the development team is aware the check is absent, making this a known gap rather than a theoretical edge case.

---

### Recommendation

In `execute_replace_class`, before updating `contract_state_changes`, verify that the new class hash exists in `contract_class_changes` by performing a `dict_read` on it and asserting the result is non-zero (i.e., a compiled class hash has been registered for it):

```cairo
// Verify the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant enforced in `execute_declare_transaction`, where `prev_value=0` and `assert_not_zero(compiled_class_hash)` together guarantee only valid, declared classes are registered.

---

### Proof of Concept

1. Declare class `A` (valid, compiled class hash registered in `contract_class_changes`).
2. Deploy contract `C` using class `A`. Contract `C` holds user funds (e.g., acts as a vault).
3. Contract `C` executes a call to `replace_class(0xdeadbeef)`, where `0xdeadbeef` is never declared.
4. The OS executes `execute_replace_class`:
   - Reads `request.class_hash = 0xdeadbeef`.
   - Skips the missing existence check (line 898 TODO).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` for contract `C`.
5. The block is proven. The state root now encodes `C.class_hash = 0xdeadbeef`.
6. Any subsequent `invoke` or `call_contract` targeting `C` reads `class_hash = 0xdeadbeef` from `contract_state_changes` and attempts to dispatch. No entry points exist for this class. Execution fails.
7. All funds in contract `C` are permanently frozen with no recovery path.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L250-292)
```text
// Performs the commitment tree updates required for (validating and) updating the
// contract class tree.
// Returns a CommitmentUpdate struct for the tree.
//
// Assumption: The dictionary `class_changes_start` is squashed.
func compute_class_commitment{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    class_changes_start: DictAccess*,
    n_class_updates: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> (contract_class_tree_update_output: CommitmentUpdate) {
    alloc_locals;

    // Guess the initial and final roots of the contract class tree.
    local initial_root;
    local final_root;
    %{ SetPreimageForClassCommitments %}

    // Create a dictionary mapping class hash to the contract class leaf hash,
    // to prepare the input for the commitment tree update.
    let (local hashed_class_changes: DictAccess*) = alloc();
    hash_class_changes(
        n_class_updates=n_class_updates,
        class_changes=class_changes_start,
        hashed_class_changes=hashed_class_changes,
    );

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants_with_poseidon(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_class_changes,
        n_updates=n_class_updates,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (
        contract_class_tree_update_output=CommitmentUpdate(
            initial_root=initial_root, final_root=final_root
        ),
    );
}
```
