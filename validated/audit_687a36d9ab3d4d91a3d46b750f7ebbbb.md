### Title
Undeclared Class Hash Accepted by `execute_replace_class` Without Validation — Permanent Fund Freezing (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared contract class. This is an explicit, acknowledged gap (marked with a `TODO` in the source). A contract can replace its own class hash with an undeclared value, permanently rendering itself uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no on-chain validation:

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

The TODO comment explicitly acknowledges the missing check. The OS commits whatever felt value is supplied as the new class hash into the state tree.

When any future transaction attempts to call this contract, `execute_entry_point` performs a `dict_read` on `contract_class_changes` using the stored (invalid) class hash:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

An undeclared class hash maps to `compiled_class_hash = 0` (the dict default). `find_element` with key `0` will fail to locate a compiled class fact, making the contract permanently uncallable at the OS level. The state commitment (`compute_contract_state_commitment`) faithfully records the corrupted class hash into the Patricia Merkle tree, making the damage irreversible. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds user funds (ERC20 token contracts, vaults, AMM pools, escrows) and calls `replace_class` with an undeclared hash will have its class hash permanently corrupted in the global state tree. Because the state commitment is a cryptographic Merkle root, and the OS has already accepted and proven the transition, there is no rollback mechanism. All assets stored in that contract's storage become permanently inaccessible.

---

### Likelihood Explanation

The `replace_class` syscall is a standard, documented StarkNet syscall available to any contract. A malicious contract deployer who attracts user deposits (e.g., by deploying a DeFi protocol) can call `replace_class` with an arbitrary felt value in a single transaction, immediately freezing all deposited funds. No privileged role, leaked key, or external dependency is required — only the ability to deploy a contract and attract deposits. The missing validation is confirmed by the in-source TODO comment, meaning the OS has never enforced this invariant.

---

### Recommendation

Before committing the `replace_class` result to `contract_state_changes`, the OS must verify that `request.class_hash` exists as a key in `contract_class_changes` (i.e., it has been declared in the current or a prior block). The check should assert that `dict_read(contract_class_changes, request.class_hash) != 0` (non-zero compiled class hash), mirroring the invariant already enforced for `execute_declare_transaction` via `prev_value=0`. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys contract `VaultA` (holds user ETH/ERC20 deposits). Users deposit funds.
2. Attacker calls any entry point on `VaultA` that internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary, never-declared felt).
3. The OS `execute_replace_class` function writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` with no validation.
4. `compute_contract_state_commitment` hashes this entry and updates the global Patricia tree root. The block is proven and finalized.
5. In any subsequent block, any transaction targeting `VaultA` reaches `execute_entry_point`, performs `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`, then calls `find_element(..., key=0)` → no compiled class found → OS-level failure.
6. The sequencer permanently excludes all calls to `VaultA`. All user funds are frozen with no recovery path. [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
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
