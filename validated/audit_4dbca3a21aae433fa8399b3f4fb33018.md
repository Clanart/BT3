### Title
Incorrect Field Ordering and Missing `account_deployment_data` Hash in `compute_deploy_account_transaction_hash` - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_deploy_account_transaction_hash` computes the deploy-account v3 transaction hash with two structural mismatches relative to the StarkNet specification: the `h(constructor_calldata)` nested hash is inserted **before** `class_hash` and `contract_address_salt` instead of after them, and the required `h(account_deployment_data)` element is **entirely absent** from the hash. This is the direct analog of the EIP-712 `TXTYPE_HASH` mismatch: a hash-domain constant/structure that does not match the actual encoded parameters.

---

### Finding Description

In `transaction_hash.cairo`, `compute_deploy_account_transaction_hash` is defined as:

```cairo
func compute_deploy_account_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*, calldata_size: felt, calldata: felt*
) -> felt {
    ...
    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        // Hash and add the constructor calldata to the hash state.
        poseidon_hash_update_with_nested_hash(data_ptr=&calldata[2], data_length=calldata_size - 2);
        // Add the class hash and the contract address salt to the hash state.
        poseidon_hash_update(data_ptr=calldata, data_length=2);
    }
    ...
}
```

The `calldata` array passed in from `execute_deploy_account_transaction` is laid out as `[class_hash, salt, constructor_calldata_0, ...]`:

```cairo
assert validate_deploy_calldata[0] = constructor_execution_context.class_hash;
assert validate_deploy_calldata[1] = salt;
memcpy(dst=&validate_deploy_calldata[2], src=constructor_execution_context.calldata, ...);
```

So the OS computes:

```
poseidon(prefix, version, sender_address, fee_hash, paymaster_hash,
         chain_id, nonce, da_modes,
         poseidon(constructor_calldata),   ← wrong position
         class_hash,                       ← raw
         salt)                             ← raw
         // h(account_deployment_data) is entirely absent
```

The StarkNet v3 deploy-account specification requires:

```
poseidon(prefix, version, sender_address, fee_hash, paymaster_hash,
         chain_id, nonce, da_modes,
         poseidon(account_deployment_data),  ← missing
         class_hash,
         salt,
         poseidon(constructor_calldata))     ← must be last
```

**Two concrete mismatches:**

1. `h(account_deployment_data)` is completely absent. The function signature does not even accept this parameter, unlike `compute_invoke_transaction_hash` and `compute_declare_transaction_hash`, which both include it.
2. `h(constructor_calldata)` is hashed **before** `class_hash` and `salt`, not after them.

Comparing with the sibling functions confirms the expected pattern:

- `compute_declare_transaction_hash`: `common_fields → h(account_deployment_data) → class_hash → compiled_class_hash`
- `compute_invoke_transaction_hash`: `common_fields → h(account_deployment_data) → h(calldata) → [h(proof_facts)]`
- `compute_deploy_account_transaction_hash` (actual): `common_fields → h(constructor_calldata) → class_hash → salt` ← **wrong**

---

### Impact Explanation

The transaction hash is the message that the account contract's `__validate_deploy__` entry point verifies a signature against. Any wallet or SDK that implements the correct StarkNet specification will:

1. Compute `H_spec` (correct order) and sign it.
2. Submit the deploy-account transaction.
3. The OS computes `H_os` (wrong order, missing field) and verifies the signature against `H_os`.
4. `H_spec ≠ H_os` → signature verification fails → transaction reverts.

In StarkNet, users routinely pre-fund a counterfactual account address before deploying it (the address is deterministic from `class_hash` and `salt`). If the deploy-account transaction always fails signature verification for spec-compliant wallets, the pre-funded ETH/STRK at that address is **permanently frozen**: the account can never be deployed, and no other transaction can spend those funds. This satisfies **Critical: Permanent freezing of funds**.

---

### Likelihood Explanation

- The entry path requires no privilege: any unprivileged user deploying an account contract is affected.
- Any wallet or SDK that independently implements the StarkNet v3 transaction hash specification (rather than copying the buggy OS reference) will produce a signature that the OS rejects.
- The bug is in the OS-level hash computation, which is the authoritative on-chain verifier. There is no user-side workaround.
- The `account_deployment_data` field is already correctly handled in `compute_invoke_transaction_hash` and `compute_declare_transaction_hash`, confirming this is an omission rather than an intentional design choice.

---

### Recommendation

Correct `compute_deploy_account_transaction_hash` to match the specification:

1. Add `account_deployment_data_size: felt` and `account_deployment_data: felt*` parameters (mirroring `compute_invoke_transaction_hash` and `compute_declare_transaction_hash`).
2. Hash `h(account_deployment_data)` immediately after `hash_tx_common_fields`.
3. Hash `class_hash` and `salt` next (raw, as single field elements).
4. Hash `h(constructor_calldata)` last as a nested hash.

```cairo
func compute_deploy_account_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    class_hash: felt,
    contract_address_salt: felt,
    constructor_calldata_size: felt,
    constructor_calldata: felt*,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
) -> felt {
    ...
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        poseidon_hash_update_single(item=class_hash);
        poseidon_hash_update_single(item=contract_address_salt);
        poseidon_hash_update_with_nested_hash(
            data_ptr=constructor_calldata, data_length=constructor_calldata_size
        );
    }
    ...
}
```

---

### Proof of Concept

**Step 1.** Observe the calldata layout in `execute_deploy_account_transaction`: [1](#0-0) 

`validate_deploy_calldata = [class_hash, salt, constructor_calldata...]`

**Step 2.** Observe the hash computation in `compute_deploy_account_transaction_hash`: [2](#0-1) 

- Line 254: `poseidon_hash_update_with_nested_hash(data_ptr=&calldata[2], ...)` → `h(constructor_calldata)` added **first**
- Line 256: `poseidon_hash_update(data_ptr=calldata, data_length=2)` → `class_hash, salt` added **after**
- No `h(account_deployment_data)` anywhere in the function

**Step 3.** Compare with `compute_declare_transaction_hash`, which correctly places `h(account_deployment_data)` before `class_hash`: [3](#0-2) 

**Step 4.** Compare with `compute_invoke_transaction_hash`, which also correctly places `h(account_deployment_data)` before `h(calldata)`: [4](#0-3) 

**Step 5.** A spec-compliant wallet signs `H_spec = poseidon(..., h([]), class_hash, salt, h(constructor_calldata))`. The OS verifies against `H_os = poseidon(..., h(constructor_calldata), class_hash, salt)`. Since `H_spec ≠ H_os`, `__validate_deploy__` fails. Pre-funded ETH/STRK at the counterfactual address is permanently frozen.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L183-217)
```text
func compute_invoke_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    execution_context: ExecutionContext*,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
    proof_facts_size: felt,
    proof_facts: felt*,
) -> felt {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
    assert account_deployment_data_size = 0;
    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

    let hash_state: PoseidonHashState = poseidon_hash_init();
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
    }
    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);
    return transaction_hash;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L264-292)
```text
func compute_declare_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    class_hash: felt,
    compiled_class_hash: felt,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
) -> felt {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
    assert account_deployment_data_size = 0;
    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

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
}
```
