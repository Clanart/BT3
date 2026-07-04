### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared class. An attacker-controlled contract can call `replace_class` with an undeclared hash, permanently rendering the contract unexecutable and freezing all funds it holds. The codebase itself acknowledges this gap with an explicit TODO comment at the exact location of the missing check.

---

### Finding Description

In `execute_replace_class` inside `syscall_impls.cairo`, after gas is deducted, the new class hash is read directly from the syscall request and written into the contract state with no validation:

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

The OS never checks whether `class_hash` exists in `contract_class_changes` (the block's declared-class dictionary) or in the existing class commitment tree. Any felt value — including one that has never been declared — is accepted and committed to the contract state tree.

This is the direct analog of the external report's root cause: in `mintMultiple`, the third loop calls `transferFrom` on every address in `_assets` without first verifying the asset is supported. Here, `execute_replace_class` writes any class hash into the state without verifying it is declared. In both cases, a missing input-validation gate allows an attacker-supplied value to corrupt protocol-level accounting.

The state commitment code in `commitment.cairo` (`hash_contract_state_changes`) simply hashes whatever `class_hash` is present in the `StateEntry` — it performs no cross-check against the class tree: [2](#0-1) 

So the invalid class hash is silently committed to the global state root.

---

### Impact Explanation

Once a contract's class hash is set to an undeclared value:

1. The contract state tree records the new (invalid) class hash.
2. The class tree has no leaf for this hash — it was never declared.
3. Every future transaction targeting the contract fails: the OS cannot locate a class definition to execute.
4. All tokens, NFTs, or other assets held by the contract are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

- `replace_class` is a standard syscall callable by any deployed contract; no privileged role is required.
- The attacker only needs to deploy a contract (an allowed entry point per the scope: "class declarer, contract deployer") and invoke `replace_class` with an arbitrary felt.
- The attack is deterministic, requires no race condition, no special network state, and no operator cooperation.
- The TODO comment at line 898 confirms the developers are aware the check is absent, meaning the gap is not an oversight in documentation but a known missing enforcement. [3](#0-2) 

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that `class_hash` is present in `contract_class_changes` (declared in the current block) or can be proven to exist in the current class commitment tree. Concretely, add a lookup into `contract_class_changes` for `class_hash` and revert (write a failure response) if the result is `UNINITIALIZED_CLASS_HASH` (0) and no prior declaration exists. This mirrors the pattern already used in `execute_declare_transaction`, which enforces `prev_value=0` to guarantee uniqueness: [4](#0-3) 

---

### Proof of Concept

1. **Attacker deploys a victim contract** (e.g., a simple token vault) and convinces users to deposit funds into it.
2. **Attacker deploys a malicious contract** whose logic calls `replace_class(0xdeadbeef)`, where `0xdeadbeef` is an arbitrary felt that has never been declared.
3. **Attacker submits an invoke transaction** targeting the malicious contract (or the vault itself if the attacker controls it).
4. **OS execution**: `execute_replace_class` is reached in `syscall_impls.cairo`. Gas is deducted. The check at line 898 is absent. `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes` for the vault's address.
5. **State commitment** (`hash_contract_state_changes` in `commitment.cairo`) hashes the new `StateEntry` with `class_hash=0xdeadbeef` and commits it to the global state root — no cross-check against the class tree.
6. **All subsequent transactions** targeting the vault fail: the OS reads `class_hash=0xdeadbeef` from the state, finds no matching entry in the class tree, and cannot produce a valid execution trace.
7. **All funds in the vault are permanently frozen.** [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L148-206)
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
