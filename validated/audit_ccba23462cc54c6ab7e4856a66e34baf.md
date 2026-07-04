### Title
Wrong Field Ordering in `compute_deploy_account_transaction_hash` Produces Incorrect Transaction Hash — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_deploy_account_transaction_hash` function computes the Poseidon hash of a `deploy_account` v3 transaction with the wrong field ordering. Specifically, `nested_hash(constructor_calldata)` is inserted **before** `class_hash` and `contract_address_salt`, and `nested_hash(account_deployment_data)` is omitted entirely. This produces a hash that will never match the hash computed by any conforming client, causing every `deploy_account` transaction included in a block to make that block unprovable, halting the network.

---

### Finding Description

The StarkNet v3 `deploy_account` transaction hash specification (SNIP-8) mandates the following Poseidon hash input sequence after the common fields:

```
h(account_deployment_data), class_hash, contract_address_salt, h(constructor_calldata)
```

The sibling functions `compute_invoke_transaction_hash` and `compute_declare_transaction_hash` both follow this pattern correctly.

**`compute_invoke_transaction_hash` (correct):** [1](#0-0) 

```cairo
hash_tx_common_fields(common_fields=common_fields);
poseidon_hash_update_with_nested_hash(
    data_ptr=account_deployment_data, data_length=account_deployment_data_size
);
poseidon_hash_update_with_nested_hash(
    data_ptr=execution_context.calldata, data_length=execution_context.calldata_size
);
```

**`compute_declare_transaction_hash` (correct):** [2](#0-1) 

```cairo
hash_tx_common_fields(common_fields=common_fields);
poseidon_hash_update_with_nested_hash(
    data_ptr=account_deployment_data, data_length=account_deployment_data_size
);
poseidon_hash_update_single(item=class_hash);
poseidon_hash_update_single(item=compiled_class_hash);
```

**`compute_deploy_account_transaction_hash` (WRONG):** [3](#0-2) 

```cairo
hash_tx_common_fields(common_fields=common_fields);
// Hash and add the constructor calldata to the hash state.
poseidon_hash_update_with_nested_hash(data_ptr=&calldata[2], data_length=calldata_size - 2);
// Add the class hash and the contract address salt to the hash state.
poseidon_hash_update(data_ptr=calldata, data_length=2);
```

The `calldata` array passed in from `execute_deploy_account_transaction` is structured as: [4](#0-3) 

```
calldata[0] = class_hash
calldata[1] = salt
calldata[2..] = constructor_calldata
```

So the OS actually computes:

```
poseidon(
  common_fields...,
  h(constructor_calldata),   ← wrong position
  class_hash,                ← wrong position
  salt,                      ← wrong position
  // h(account_deployment_data) is entirely absent
)
```

Instead of the spec-required:

```
poseidon(
  common_fields...,
  h(account_deployment_data),
  class_hash,
  salt,
  h(constructor_calldata),
)
```

Two distinct deviations from the spec:
1. `h(account_deployment_data)` is never added to the hash state (it is always zero for deploy_account, but `poseidon_hash_update_with_nested_hash` of an empty array still contributes a non-trivial element).
2. `h(constructor_calldata)` is placed **before** `class_hash` and `salt` instead of after them.

---

### Impact Explanation

The OS Cairo program is the authoritative prover for every StarkNet block. When a `deploy_account` transaction is included in a block, `compute_deploy_account_transaction_hash` is called and its result is checked against the client-supplied hash via the `%{ AssertTransactionHash %}` hint: [5](#0-4) 

Because the OS-computed hash will never equal the client-computed hash (the field ordering is different), this hint will always fail for any `deploy_account` transaction. A hint failure aborts the Cairo execution, making the block **unprovable**. Any block containing even a single `deploy_account` transaction cannot be proven, causing a **total network halt** for as long as such transactions are included.

**Allowed impact matched:** High — Network not being able to confirm new transactions (total network shutdown).

---

### Likelihood Explanation

The entry path requires no privilege whatsoever. Any user can submit a `deploy_account` transaction through the standard JSON-RPC interface. The sequencer will accept and include it in a block (it is a valid, well-formed transaction). The bug is then triggered deterministically during proving. There is no randomness, no race condition, and no special state required. Likelihood is **high**.

---

### Recommendation

Rewrite `compute_deploy_account_transaction_hash` to match the SNIP-8 specification and the pattern used by the sibling functions:

```cairo
let hash_state: PoseidonHashState = poseidon_hash_init();
with hash_state {
    hash_tx_common_fields(common_fields=common_fields);
    // 1. account_deployment_data (always empty for deploy_account, but must be hashed).
    poseidon_hash_update_with_nested_hash(
        data_ptr=account_deployment_data, data_length=account_deployment_data_size
    );
    // 2. class_hash (calldata[0]).
    poseidon_hash_update_single(item=calldata[0]);
    // 3. contract_address_salt (calldata[1]).
    poseidon_hash_update_single(item=calldata[1]);
    // 4. constructor_calldata (calldata[2..]).
    poseidon_hash_update_with_nested_hash(
        data_ptr=&calldata[2], data_length=calldata_size - 2
    );
}
```

The function signature should also accept `account_deployment_data_size` and `account_deployment_data` parameters, consistent with `compute_invoke_transaction_hash` and `compute_declare_transaction_hash`.

---

### Proof of Concept

1. A user constructs a valid `deploy_account` v3 transaction. The client computes the transaction hash per the SNIP-8 spec:
   ```
   h = poseidon("deploy_account", 3, sender, fee_hash, paymaster_hash,
                chain_id, nonce, da_modes,
                h([]),          // account_deployment_data
                class_hash,
                salt,
                h(constructor_calldata))
   ```
   The user signs this hash and submits the transaction.

2. The sequencer accepts the transaction (it is structurally valid) and includes it in a block.

3. The OS Cairo program runs `compute_deploy_account_transaction_hash`, which produces:
   ```
   h = poseidon("deploy_account", 3, sender, fee_hash, paymaster_hash,
                chain_id, nonce, da_modes,
                h(constructor_calldata),   // ← wrong position
                class_hash,                // ← wrong position
                salt)                      // ← wrong position, h([]) missing
   ``` [6](#0-5) 

4. The `%{ AssertTransactionHash %}` hint compares the OS-computed hash against the client-supplied hash. They differ. The hint raises an exception, aborting the Cairo execution.

5. The block cannot be proven. The network halts.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L200-213)
```text
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        poseidon_hash_update_with_nested_hash(
            data_ptr=execution_context.calldata, data_length=execution_context.calldata_size
        );
        // For backward compatibility, we don't hash proof facts if they are empty.
        if (proof_facts_size != 0) {
            poseidon_hash_update_with_nested_hash(
                data_ptr=proof_facts, data_length=proof_facts_size
            );
        }
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L279-291)
```text
    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        // Add the class hash to the hash state.
        poseidon_hash_update_single(item=class_hash);
        poseidon_hash_update_single(item=compiled_class_hash);
    }
    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);

    return transaction_hash;
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
