### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Bricking and Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` from the contract's syscall request without validating that the hash corresponds to a previously declared class. This is explicitly acknowledged by a TODO comment in the code. An attacker-controlled contract can call `replace_class` with any undeclared class hash, causing the OS to commit an invalid class hash into the state. The contract becomes permanently unexecutable, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in the declared class tree:

```cairo
func execute_replace_class{...}(contract_address: felt) {
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
``` [1](#0-0) 

The `class_hash` field comes directly from the attacker-controlled syscall request segment. The only gas check performed is `reduce_syscall_gas_and_write_response_header`, which does not validate the semantic content of the request. No cross-reference against `contract_class_changes` (the declared class dictionary) is performed. [2](#0-1) 

This is structurally identical to the external report's root cause: an attacker-controlled parameter (`class_hash` here, `quoteAsset` there) is used to update a critical accounting/state variable without validating that the parameter is legitimate.

The `contract_class_changes` dictionary, which tracks declared classes, is available as an implicit argument throughout the OS execution context but is never consulted inside `execute_replace_class`. [3](#0-2) 

---

### Impact Explanation

Once the OS produces a valid proof committing a contract's class hash to an undeclared value, the state is finalized on L1. Any subsequent transaction targeting that contract will fail at class resolution because the class does not exist in the class tree. All funds (ETH, ERC20 tokens, or any assets) held by the contract are permanently inaccessible — there is no recovery path once the state root is accepted by the L1 verifier.

This satisfies the **Critical: Permanent freezing of funds** impact category.

The state commitment logic in `commitment.cairo` hashes the `class_hash` field from `StateEntry` directly into the Patricia tree leaf without any validity check on the class hash value itself: [4](#0-3) 

So the invalid class hash propagates into the final state root, which is then verified on L1.

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer:

1. Attacker deploys a contract (e.g., a token vault, multisig, or liquidity pool) that accepts user deposits.
2. Users deposit funds into the contract.
3. Attacker triggers the contract to call `replace_class` with an arbitrary felt value that is not a declared class hash (e.g., `1` or any random felt).
4. The OS processes the syscall, updates `contract_state_changes` with the invalid class hash, and produces a valid proof.
5. The L1 verifier accepts the proof; the state is finalized.
6. The contract is permanently bricked; all deposited funds are frozen.

No privileged access, leaked keys, or external dependency compromise is required. The entry point is the standard `replace_class` syscall, callable by any Sierra contract.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, validate that `class_hash` exists as a key in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `class_hash` as the key and assert the returned compiled class hash is non-zero. This is exactly what the existing TODO comment calls for:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The fix should replace this TODO with an actual assertion, for example:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

---

### Proof of Concept

1. Declare a legitimate class `C` and deploy contract `V` (a vault) using class `C`. Users deposit 1000 ETH into `V`.
2. Attacker calls a function on `V` that internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).
3. The OS executes `execute_replace_class`:
   - Reads `request.class_hash = 0xdeadbeef`.
   - Skips the missing declared-class check (the TODO).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. `state_update` in `state.cairo` squashes the dict and calls `compute_contract_state_commitment`, which hashes `0xdeadbeef` into the Patricia tree leaf — no validity check.
5. The OS outputs a valid proof. The L1 verifier accepts it.
6. All future transactions to `V` fail at class resolution. The 1000 ETH is permanently frozen. [5](#0-4) [6](#0-5)

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
