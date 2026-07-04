### Title
Missing Zero/Uninitialized Class Hash Validation in `execute_replace_class` - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

Both the new (`syscall_impls.cairo`) and deprecated (`deprecated_execute_syscalls.cairo`) implementations of `execute_replace_class` accept `class_hash = 0` (which equals `UNINITIALIZED_CLASS_HASH`) from an attacker-controlled syscall request, and write it directly into the contract's `StateEntry` without any validation. This silently "uninitializes" a live contract in the OS state, corrupting the state commitment and permanently freezing all funds held by that contract.

---

### Finding Description

`UNINITIALIZED_CLASS_HASH` is defined as `0`: [1](#0-0) 

In `execute_replace_class` (new syscall path), the `class_hash` is read directly from the user-supplied request and written to state with no zero-check: [2](#0-1) 

The comment on line 898 explicitly acknowledges the missing validation: [3](#0-2) 

The same flaw exists in the deprecated path: [4](#0-3) 

The `get_contract_state_hash` function treats `class_hash == UNINITIALIZED_CLASS_HASH (0)` with `storage_root == 0` and `nonce == 0` as an empty/non-existent contract (returning hash `0`): [5](#0-4) 

So if an attacker replaces a contract's class hash with `0`, the OS state commitment will treat that contract as non-existent, even though its storage and funds remain in the trie. The contract becomes permanently inaccessible — its entry point dispatch will fail (no class to execute), and any funds it holds are frozen.

By contrast, `deploy_contract` correctly enforces that the deployed class hash is not `UNINITIALIZED_CLASS_HASH` (it asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` as a precondition for deployment, and the new class hash comes from a validated `ExecutionContext`). The `execute_replace_class` path has no analogous guard.

---

### Impact Explanation

A contract that calls `replace_class(0)` (or is tricked into doing so via a malicious Sierra program) will have its `StateEntry.class_hash` set to `UNINITIALIZED_CLASS_HASH`. After the block is committed:

- The OS state commitment (`get_contract_state_hash`) will compute the contract's leaf hash as if it were uninitialized, corrupting the Merkle tree root.
- All subsequent calls to the contract will fail at entry-point dispatch (no class to look up), permanently freezing any ERC-20 tokens, ETH, or other assets held in the contract's storage.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

Any deployed Sierra contract that exposes a callable entry point can issue the `replace_class` syscall with `class_hash = 0`. An attacker can deploy their own contract and call `replace_class(0)` on themselves, or exploit a contract that delegates class replacement to user input. The syscall is reachable by any unprivileged transaction sender. No privileged role or operator access is required.

---

### Recommendation

Add an `assert_not_zero(class_hash)` check in both `execute_replace_class` implementations immediately after reading `class_hash` from the request, before writing to state:

In `syscall_impls.cairo` (`execute_replace_class`):
```cairo
let class_hash = request.class_hash;
// ADD: assert_not_zero(class_hash);  // Prevent replacing with UNINITIALIZED_CLASS_HASH
```

In `deprecated_execute_syscalls.cairo` (`execute_replace_class`):
```cairo
let class_hash = syscall_ptr.class_hash;
// ADD: assert_not_zero(class_hash);  // Prevent replacing with UNINITIALIZED_CLASS_HASH
```

This mirrors the pattern already used for `compiled_class_hash` in `execute_declare_transaction`: [6](#0-5) 

---

### Proof of Concept

1. Attacker deploys a Sierra contract `A` that contains an entry point calling `replace_class(class_hash=0)`.
2. Attacker sends an invoke transaction calling that entry point.
3. The OS executes `execute_replace_class` in `syscall_impls.cairo`. `request.class_hash = 0` passes through with no validation.
4. `dict_update` writes a new `StateEntry` with `class_hash = 0` for contract `A`.
5. At block commitment, `get_contract_state_hash(class_hash=0, storage_root=R, nonce=N)` — if `R != 0` or `N != 0`, the hash is computed with `class_hash=0`, producing a state root that encodes the contract as having an uninitialized class but non-empty storage.
6. All future calls to contract `A` fail: the OS looks up `state_entry.class_hash = 0`, finds no class, and cannot dispatch any entry point.
7. Any funds (ETH, ERC-20 tokens) stored in contract `A`'s storage are permanently frozen. [7](#0-6) [4](#0-3) [1](#0-0) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L51-61)
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
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-816)
```text
    assert_not_zero(compiled_class_hash);
```
