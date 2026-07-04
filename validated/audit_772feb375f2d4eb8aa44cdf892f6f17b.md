### Title
`execute_replace_class` Fails to Validate That the New Class Hash Is Declared — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

`execute_replace_class` updates a contract's class hash in the OS state without verifying that the supplied class hash corresponds to a previously declared class. An attacker-controlled contract can replace its class with an arbitrary, undeclared felt value. The OS proof is generated and accepted with this invalid state, permanently freezing any funds held by the contract.

---

### Finding Description

The vulnerability class from the reference report is: **a state-update function changes one parameter while omitting validation of a dependent constraint**. In `updateTradePosition()` the open price is updated but TP/SL limits are not re-validated. The exact structural analog exists in the StarkNet OS.

`execute_replace_class` in `syscall_impls.cairo` performs the following steps:

1. Reads `request.class_hash` — a caller-supplied felt value.
2. Reads the current `StateEntry` for the contract.
3. Writes a new `StateEntry` with `class_hash = request.class_hash`.
4. Appends a revert-log entry.

The function never consults `contract_class_changes` to confirm that `request.class_hash` was ever declared. The developer-acknowledged gap is captured in the inline comment:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [1](#0-0) 

After this syscall succeeds, the contract's leaf in the Patricia state tree commits to a class hash that has no corresponding compiled class. Any future call to the contract will fail to load the class, making the contract permanently inoperable.

For comparison, `execute_deploy` (the deploy path) enforces `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing the new class hash, showing that the OS does enforce class-hash constraints in other state-update paths — but not in `replace_class`. [2](#0-1) 

The `StateEntry` struct that is committed to the Merkle tree contains `class_hash` as a first-class field: [3](#0-2) 

`get_contract_state_hash` hashes `class_hash` directly into the leaf, so the invalid hash is permanently committed to the global state root: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once the OS proof is generated and verified on L1, the global state root reflects the contract with an undeclared class hash. The contract can no longer execute any entry point (no class to load), so it cannot transfer, withdraw, or otherwise move any tokens it holds. There is no recovery path: `replace_class` itself requires the contract to execute, which is impossible once the class hash is invalid.

---

### Likelihood Explanation

**Low.**

The attacker must be a contract deployer (explicitly in scope). The attack requires:
1. Deploying a contract that appears legitimate and accumulates user funds.
2. Calling `replace_class` with an arbitrary undeclared felt as the class hash.

No privileged operator access, leaked key, or external dependency is required. The syscall is available to any Sierra contract. The barrier is social engineering users into depositing funds before the attack is executed.

---

### Recommendation

Inside `execute_replace_class`, after reading `request.class_hash`, verify that the hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). The function signature already has access to `contract_state_changes`; `contract_class_changes` should be added as an implicit argument and a `dict_read` performed to assert the compiled class hash is non-zero for the requested class hash. This mirrors the pattern used in `execute_declare_transaction`, which enforces `prev_value=0` to guarantee class uniqueness. [5](#0-4) 

---

### Proof of Concept

1. Attacker deploys contract `C` (class hash `H_valid`, declared). Users deposit ERC-20 tokens into `C`.
2. Attacker calls an entry point on `C` that internally invokes the `replace_class` syscall with `new_class_hash = 0xdeadbeef` (never declared).
3. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No check against `contract_class_changes` is performed.
   - `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` for contract `C`.
4. The block is proven; the global state root now commits `C → 0xdeadbeef`.
5. Any subsequent `call_contract` or `invoke` targeting `C` fails at class loading — no entry point can execute.
6. All ERC-20 balances stored in `C`'s storage are permanently frozen. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-66)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
