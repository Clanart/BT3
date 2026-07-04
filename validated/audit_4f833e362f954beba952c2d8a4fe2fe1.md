### Title
Reversed Field Order in `compute_deploy_account_transaction_hash` Produces Invalid Transaction Hash — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

In `compute_deploy_account_transaction_hash`, the constructor calldata nested hash is added to the Poseidon hash state **before** the class hash and contract address salt, which is the reverse of the order mandated by the StarkNet v3 transaction hash specification. Every `deploy_account` transaction processed by the OS will produce a hash that differs from the hash the client signed, causing `__validate_deploy__` signature verification to fail for every such transaction.

---

### Finding Description

`compute_deploy_account_transaction_hash` receives a `calldata` array whose layout is:

```
calldata[0]  = class_hash
calldata[1]  = contract_address_salt
calldata[2:] = constructor_calldata
```

The function builds the Poseidon hash state as follows:

```cairo
// Hash and add the constructor calldata to the hash state.
poseidon_hash_update_with_nested_hash(data_ptr=&calldata[2], data_length=calldata_size - 2);
// Add the class hash and the contract address salt to the hash state.
poseidon_hash_update(data_ptr=calldata, data_length=2);
```

This appends fields in the order:

1. `h(constructor_calldata)` (nested hash)
2. `class_hash`
3. `contract_address_salt`

The StarkNet v3 deploy_account hash specification (SNIP-8) requires the opposite order:

1. `class_hash`
2. `contract_address_salt`
3. `h(constructor_calldata)`

The two `poseidon_hash_update*` calls are in the wrong order relative to each other. [1](#0-0) 

The calldata array is assembled correctly in `execute_deploy_account_transaction` (class_hash at index 0, salt at index 1, constructor calldata at index 2+), confirming the bug is solely in the hash computation order: [2](#0-1) 

The OS-computed hash is then stored in `TxInfo.transaction_hash` via `fill_account_tx_info` and passed to the account contract's `__validate_deploy__` entry point: [3](#0-2) [4](#0-3) 

---

### Impact Explanation

The account contract's `__validate_deploy__` receives a `TxInfo` struct containing the OS-computed (wrong) transaction hash. The account contract verifies the user's ECDSA/Stark signature against this hash. Because the client signed the hash computed in the correct field order, and the OS provides a hash computed in the wrong order, the two values will never match. `__validate_deploy__` will revert for every `deploy_account` transaction, making it impossible to deploy any new account contract on the network via this transaction type. This maps to the allowed impact: **High — Network not being able to confirm new transactions** (all `deploy_account` transactions are permanently broken at the OS level).

---

### Likelihood Explanation

Every `deploy_account` v3 transaction triggers this code path unconditionally. There is no version gate, flag, or fallback that bypasses `compute_deploy_account_transaction_hash`. Any unprivileged user who submits a `deploy_account` transaction will hit this bug. Likelihood is **High**.

---

### Recommendation

Swap the two `poseidon_hash_update*` calls so that `class_hash` and `contract_address_salt` are added to the hash state before the nested hash of the constructor calldata, matching the SNIP-8 specification:

```cairo
// Add the class hash and the contract address salt to the hash state.
poseidon_hash_update(data_ptr=calldata, data_length=2);
// Hash and add the constructor calldata to the hash state.
poseidon_hash_update_with_nested_hash(data_ptr=&calldata[2], data_length=calldata_size - 2);
```

---

### Proof of Concept

1. A user constructs a `deploy_account` v3 transaction with:
   - `class_hash = C`, `contract_address_salt = S`, `constructor_calldata = [D]`
2. The client computes the hash per spec: `Poseidon(..., C, S, h([D]))` and signs it → signature `σ`.
3. The OS calls `compute_deploy_account_transaction_hash`, which computes `Poseidon(..., h([D]), C, S)` — a different value.
4. The OS stores this wrong hash in `TxInfo` and calls `__validate_deploy__`.
5. The account contract verifies `σ` against `Poseidon(..., h([D]), C, S)` — verification fails.
6. The transaction reverts. This is deterministic for every `deploy_account` transaction, permanently blocking new account deployments. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L241-261)
```text
func compute_deploy_account_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*, calldata_size: felt, calldata: felt*
) -> felt {
    alloc_locals;

    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        // Hash and add the constructor calldata to the hash state.
        poseidon_hash_update_with_nested_hash(data_ptr=&calldata[2], data_length=calldata_size - 2);
        // Add the class hash and the contract address salt to the hash state.
        poseidon_hash_update(data_ptr=calldata, data_length=2);
    }

    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);
    return transaction_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L587-595)
```text
    local validate_deploy_calldata_size = constructor_execution_context.calldata_size + 2;
    let (validate_deploy_calldata: felt*) = alloc();
    assert validate_deploy_calldata[0] = constructor_execution_context.class_hash;
    assert validate_deploy_calldata[1] = salt;
    memcpy(
        dst=&validate_deploy_calldata[2],
        src=constructor_execution_context.calldata,
        len=constructor_execution_context.calldata_size,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L607-615)
```text
        let transaction_hash = compute_deploy_account_transaction_hash(
            common_fields=common_tx_fields,
            calldata_size=validate_deploy_calldata_size,
            calldata=validate_deploy_calldata,
        );
    }
    update_poseidon_in_builtin_ptrs(poseidon_ptr=poseidon_ptr);

    %{ AssertTransactionHash %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L621-631)
```text
    fill_account_tx_info(
        transaction_hash=transaction_hash,
        common_tx_fields=common_tx_fields,
        account_deployment_data_size=0,
        account_deployment_data=cast(0, felt*),
        proof_facts_size=0,
        proof_facts=cast(0, felt*),
        tx_info_dst=tx_info,
        deprecated_tx_info_dst=deprecated_tx_info,
    );

```
