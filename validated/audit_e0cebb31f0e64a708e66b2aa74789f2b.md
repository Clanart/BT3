### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation does not verify that the caller-supplied class hash corresponds to a previously declared contract class. Any contract can replace its own class hash with an arbitrary, undeclared felt value, rendering itself permanently non-executable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, after deducting gas, the function reads the caller-supplied `class_hash` from the syscall request and immediately writes it into `contract_state_changes` with no validation against the set of declared classes:

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

The TODO comment on line 898 explicitly acknowledges the missing check. The `contract_class_changes` dict — which tracks declared classes — is not an implicit argument of `execute_replace_class` and is never consulted. [2](#0-1) 

By contrast, the `execute_declare_transaction` path enforces `prev_value=0` to prevent re-declaration and verifies the class hash pre-image via `finalize_class_hash`, but `replace_class` bypasses all of this. [3](#0-2) 

When a subsequent transaction targets the now-corrupted contract, the OS reads the stored (invalid) class hash from `contract_state_changes` and attempts to dispatch execution against it: [4](#0-3) 

Because no class with that hash was ever declared, execution cannot proceed. The contract is permanently bricked.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds (native ETH, ERC-20 tokens, or other assets) held in the storage of the affected contract become permanently inaccessible. There is no recovery path: the class hash is committed to the global state root via the Patricia tree update in `compute_contract_state_commitment`, and no mechanism exists to revert a committed state change after the block is finalized. [5](#0-4) 

---

### Likelihood Explanation

The `replace_class` syscall is available to any executing contract with no privilege restriction. The attacker-controlled entry path is:

1. An unprivileged user deploys a contract `C` (e.g., a vault or escrow) that exposes a `replace_class` call path — either intentionally (rug-pull) or via a logic bug.
2. Users deposit funds into `C`.
3. The attacker invokes the function that calls `replace_class(arbitrary_undeclared_hash)`.
4. The OS processes the syscall through `execute_replace_class` without validation.
5. `C`'s class hash in `contract_state_changes` is set to the undeclared value.
6. All future calls to `C` fail; funds are permanently frozen.

No privileged role, leaked key, or external dependency is required. The vulnerability is reachable by any transaction sender who controls or can influence a contract that calls `replace_class`.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes` (i.e., it was previously declared). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero (`UNINITIALIZED_CLASS_HASH` check). This is precisely the check the TODO comment defers.

`execute_replace_class` must receive `contract_class_changes` as an implicit argument to perform this lookup.

---

### Proof of Concept

1. Deploy contract `Vault` that accepts ETH deposits and exposes an external function `brick()` which calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (never declared).
2. Users call `deposit()` on `Vault`, locking funds in its storage.
3. Attacker submits an invoke transaction calling `Vault::brick()`.
4. The OS executes `execute_replace_class` via `execute_syscalls`: [6](#0-5) 

5. `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes` for `Vault`'s address with no validation.
6. The block is finalized; the new state root commits `Vault`'s class hash as `0xdeadbeef`.
7. Any subsequent invoke targeting `Vault` reads `class_hash=0xdeadbeef` from state, finds no declared class, and fails permanently.
8. All deposited funds are frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-884)
```text
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L463-473)
```text
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );
    let (tx_info_ptr: TxInfo*) = alloc();
    let (deprecated_tx_info_ptr: DeprecatedTxInfo*) = alloc();
    local calldata_size;
    local calldata: felt*;
    %{ TxCalldata %}
    local tx_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=entry_point_type,
        class_hash=state_entry.class_hash,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
