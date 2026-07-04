### Title
Missing Class Hash Validation in `replace_class` Syscall Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that the hash corresponds to a previously declared contract class. This is directly analogous to the `amountOutMin = 0` pattern in the reference report: a critical parameter controlling a state-changing operation is accepted without a minimum validity bound. An unprivileged contract deployer can exploit this to permanently freeze funds held in any contract they control.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS processes the `replace_class` syscall by reading `request.class_hash` directly from the syscall pointer and writing it into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (the set of declared classes):

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

The inline `TODO` comment explicitly acknowledges the missing check. The `class_hash` field is a raw felt from the caller's syscall segment — it is never validated against `contract_class_changes`. Accepted values include:

- `0` — the sentinel `UNINITIALIZED_CLASS_HASH` defined in `commitment.cairo` line 16
- Any arbitrary felt that has never been declared

Once the state transition is committed and proven, the contract's class hash in the global state tree is permanently set to the invalid value. Because the OS uses the class hash to look up compiled class facts at execution time, all future entry-point calls to that contract will fail to find a matching class and will revert. The contract's storage (and any token balances it holds) becomes permanently inaccessible. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is overwritten with an undeclared value and the block is proven and accepted on L1, the state root reflects the invalid class hash. There is no on-chain mechanism to revert a finalized state root. Any ERC-20 balances, NFTs, or other assets stored in the contract's storage trie are permanently inaccessible because every future call to the contract will fail at class resolution. The `get_contract_state_hash` function in `commitment.cairo` commits the class hash into the Merkle leaf, making the corruption part of the canonical state. [3](#0-2) 

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from its own execution context — no privileged role is required. The attacker path requires only:

1. Deploying a contract (permissionless via the `deploy` syscall or `execute_deploy_account_transaction`).
2. Attracting user deposits into the contract.
3. Invoking an entry point that calls `replace_class` with `class_hash = 0` or any undeclared felt.

Step 3 is a single syscall that costs only `REPLACE_CLASS_GAS_COST = 10670` gas units. The operation succeeds silently — the response header written at line 539 sets `failure_flag=0` unconditionally after gas reduction succeeds. There is no revert, no error, and no on-chain signal to users that the class has been corrupted. [4](#0-3) [5](#0-4) 

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, the OS must verify that `class_hash` exists as a key in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, a `dict_read` on `contract_class_changes` with `key=class_hash` should be performed and the returned `compiled_class_hash` must be non-zero (i.e., `≠ UNINITIALIZED_CLASS_HASH`). This mirrors the existing check in `execute_declare_transaction` at line 816:

```cairo
assert_not_zero(compiled_class_hash);
```

The same guard must be applied in `execute_replace_class` before the `dict_update` call. [6](#0-5) 

---

### Proof of Concept

1. Attacker deploys contract `VaultAttacker` with a `deposit()` entry point that accepts STRK and a `rug()` entry point that calls `replace_class(class_hash=0)`.
2. Users call `deposit()`, transferring funds into the contract's storage.
3. Attacker calls `rug()`. The OS executes `execute_replace_class` with `class_hash=0`. No validation is performed. The state entry for `VaultAttacker` is updated: `class_hash=0, storage_ptr=<existing storage>, nonce=<existing nonce>`.
4. The block is sequenced and proven. The L1 verifier accepts the proof. The state root now encodes `class_hash=0` for `VaultAttacker`.
5. Any subsequent call to `VaultAttacker` fails at class resolution — `UNINITIALIZED_CLASS_HASH=0` has no compiled class facts. All deposited funds are permanently frozen. [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L51-70)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L111-111)
```text
const REPLACE_CLASS_GAS_COST = 10670;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-818)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
```
