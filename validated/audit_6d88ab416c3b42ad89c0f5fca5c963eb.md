### Title
Missing Declared-Class Validation in `replace_class` Syscall Enables Permanent Fund Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied to the `replace_class` syscall corresponds to a previously declared contract class. Any contract can replace its own class hash with an arbitrary, undeclared value. Once this happens, all future calls to that contract fail at the class-lookup stage, permanently locking any funds (ERC20 balances, etc.) held by or accessible only through that contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall as follows:

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
``` [1](#0-0) 

The `TODO` comment at line 898 explicitly acknowledges the missing validation. The OS unconditionally writes the caller-supplied `class_hash` into `contract_state_changes` without checking whether that hash exists in the `contract_class_changes` tree (i.e., whether it was ever declared via a `DECLARE` transaction).

The `StateEntry` struct stores `class_hash` alongside `storage_ptr` and `nonce`: [2](#0-1) 

When a future transaction calls into the affected contract, the OS reads `state_entry.class_hash` and attempts to execute the corresponding class. If that class hash was never declared, execution fails unconditionally — there is no fallback and no recovery path.

The class hash is committed into the global state root via `compute_contract_state_commitment` → `hash_contract_state_changes` → `get_contract_state_hash`, which hashes `(class_hash, storage_root, nonce)` into the Patricia tree: [3](#0-2) 

Once the block is proven and the state root is committed on-chain, the invalid class hash is permanently part of the canonical state. There is no on-chain mechanism to undo it.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds accessible only through the affected contract (e.g., ERC20 balances held by the contract's address, stored in the ERC20 contract's storage keyed by the contract's address) become permanently inaccessible. The contract's storage and nonce remain in the state tree, but no entry point can ever be executed again because the class hash resolves to nothing. The funds cannot be recovered by any on-chain action.

---

### Likelihood Explanation

**High.** The `replace_class` syscall is a standard, publicly documented StarkNet syscall reachable by any contract. No privileged role is required. A malicious actor can:

1. Deploy a contract that accepts deposits from users (mimicking a legitimate DeFi protocol).
2. At any point, call `replace_class` with an arbitrary undeclared felt value (e.g., `0xdead`).
3. All deposited funds are permanently frozen.

A buggy contract could also trigger this accidentally. The OS provides no guard.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the supplied `class_hash` exists in `contract_class_changes` (i.e., it was previously declared). Concretely, perform a `dict_read` on `contract_class_changes` for the given `class_hash` and assert the returned compiled class hash is non-zero. This is exactly what the existing `TODO` comment describes and what the analogous EToken fix (disabling invalid transfers) achieves in the reference report.

---

### Proof of Concept

1. Declare a valid class `A` and deploy a contract `C` using class `A`. Contract `C` implements a `deposit` entry point that accepts STRK/ETH transfers and a `trigger` entry point.

2. Users call `deposit` on `C`, accumulating balances in the ERC20 contract's storage keyed by `C`'s address.

3. The deployer calls `trigger` on `C`. Inside `trigger`, the contract issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).

4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `contract_state_changes` is updated: `C`'s `StateEntry.class_hash` is set to `0xdeadbeef`.
   - The revert log records the old class hash (but the transaction is not reverted).

5. The block is proven. The state root now commits `C` with `class_hash = 0xdeadbeef`.

6. Any subsequent call to `C` (e.g., to withdraw deposited funds) causes the OS to look up class `0xdeadbeef`, find nothing, and fail. The funds are permanently frozen.

The root cause is the unconditional `dict_update` at line 906 of `syscall_impls.cairo` with no prior validation against `contract_class_changes`: [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L51-71)
```text
func get_contract_state_hash{hash_ptr: HashBuiltin*}(
    class_hash: felt, storage_root: felt, nonce: felt
) -> (hash: felt) {
    const CONTRACT_STATE_HASH_VERSION = 0;
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }

    // Set res = H(H(class_hash, storage_root), nonce).
    let (hash_value) = hash2(class_hash, storage_root);
    let (hash_value) = hash2(hash_value, nonce);

    // Return H(hash_value, CONTRACT_STATE_HASH_VERSION). CONTRACT_STATE_HASH_VERSION must be in the
    // outermost hash to guarantee unique "decoding".
    let (hash) = hash2(hash_value, CONTRACT_STATE_HASH_VERSION);
    return (hash=hash);
}
```
