### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt as a new class hash without verifying that the hash corresponds to a declared contract class. A malicious contract can exploit this to replace its own class hash with an undeclared value, permanently rendering the contract uncallable and freezing any funds held in its storage.

### Finding Description
In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested `class_hash` directly from the syscall request and writes it into the contract state without any validation that the hash is present in the declared class registry:

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

The developer-acknowledged TODO confirms this is a known missing invariant. The OS enforces no constraint that `class_hash` must exist in `compiled_class_facts` or `deprecated_compiled_class_facts`. The new class hash is committed to the global state tree unconditionally. [1](#0-0) 

The compiled class facts bundle is validated post-execution only for classes that were actually executed during the block: [2](#0-1) 

A class hash written via `replace_class` but never executed in the same block is never checked against the compiled class facts, so the invalid hash passes through the proof undetected.

### Impact Explanation
Once a contract's class hash is set to an undeclared value (e.g., `0xdeadbeef`):

1. The state commitment (`compute_contract_state_commitment`) hashes the new invalid class hash into the Patricia Merkle Tree and the global state root, making the change permanent on-chain.
2. Any future transaction invoking the contract causes the OS to look up the compiled class for the invalid hash. No compiled class exists, so the OS cannot produce a valid execution trace for that call.
3. The sequencer's blockifier will revert or exclude every such transaction.
4. The contract's storage — including ERC-20 token balances or ETH held via `transfer` — remains in the state but is permanently inaccessible.

This constitutes **Critical: Permanent freezing of funds**. [3](#0-2) 

### Likelihood Explanation
The attack path requires only deploying a contract and submitting a transaction. No privileged role, leaked key, or operator cooperation is needed. The `replace_class` syscall is callable by any Sierra/Cairo contract on itself. A malicious DeFi contract can accept user deposits and then atomically call `replace_class` with an invalid hash in the same transaction or a subsequent one, permanently locking all deposited assets.

### Recommendation
Add an explicit check inside `execute_replace_class` that the requested `class_hash` is present in the contract class changes dictionary (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for the requested `class_hash` and assert the result is non-zero before committing the state update. This mirrors the invariant enforced during `execute_declare_transaction`:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [4](#0-3) 

### Proof of Concept

1. **Attacker deploys** `MaliciousVault` — a contract that accepts ERC-20 deposits and exposes a `drain()` function that calls `replace_class(0x1)` (an undeclared hash).
2. **Users deposit** tokens into `MaliciousVault`, trusting its published Sierra source.
3. **Attacker calls** `drain()`. The OS executes `execute_replace_class`:
   - `class_hash = 0x1` is written to `contract_state_changes` with no validation.
   - The state diff is committed; the global state root now encodes `MaliciousVault.class_hash = 0x1`.
4. **Any subsequent call** to `MaliciousVault` (e.g., `withdraw()`) causes the OS to look up compiled class `0x1`. No such class exists in `compiled_class_facts`. The OS cannot generate a valid proof segment for the call; the sequencer reverts/excludes the transaction.
5. **All deposited tokens** remain in `MaliciousVault`'s storage forever, with no callable entry point to retrieve them. [5](#0-4)

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
