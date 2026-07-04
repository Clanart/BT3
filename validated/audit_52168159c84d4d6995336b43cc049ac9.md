### Title
`compute_meta_tx_v0_hash` Omits Nonce, Enabling Unbounded Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` constructs the hash that account contracts verify when the `meta_tx_v0` syscall is used. The hash omits a nonce (or any other replay-preventing field), and the OS never tracks or increments a nonce for version-0 meta transactions. An unprivileged attacker who observes a valid `meta_tx_v0` signature on-chain can replay it an unlimited number of times across arbitrary outer transactions, causing repeated unauthorized execution of the signed operation and direct loss of funds.

---

### Finding Description

`compute_meta_tx_v0_hash` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

```cairo
// transaction_hash.cairo L295-L315
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,           // ← no nonce
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
``` [1](#0-0) 

For comparison, `deprecated_get_transaction_hash` for a regular v1 invoke transaction passes `additional_data_size=1` with the nonce as the additional data. The meta-tx variant passes nothing. [2](#0-1) 

The resulting hash is therefore a pure function of `(contract_address, EXECUTE_ENTRY_POINT_SELECTOR, calldata, chain_id)`. Any two calls with the same tuple produce the identical hash, so the same ECDSA signature is valid for both.

`execute_meta_tx_v0` in `syscall_impls.cairo` constructs the inner `TxInfo` with `nonce=0` and never calls `check_and_increment_nonce`:

```cairo
// syscall_impls.cairo L343-L363
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,          // ← always zero, never incremented
    ...
);
``` [3](#0-2) 

`check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
// execute_transaction_utils.cairo L64-L67
func check_and_increment_nonce{...}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [4](#0-3) 

`run_validate` also skips validation for version-0 transactions, so the signature is passed directly to the called contract's `__execute__` entry point without any OS-level replay guard: [5](#0-4) 

The syscall enforces that only `EXECUTE_ENTRY_POINT_SELECTOR` may be targeted, which is precisely the entry point account contracts use to authorize fund transfers: [6](#0-5) 

---

### Impact Explanation

Any account contract that relies on the `transaction_hash` field of `TxInfo` (as exposed by `get_execution_info`) to verify a `meta_tx_v0` signature is vulnerable. Because the hash is identical for every replay of the same `(contract_address, calldata, chain_id)` tuple, an attacker can:

1. Observe a valid `meta_tx_v0` signature on-chain (it is part of the syscall request, which is public).
2. Deploy a contract that issues the same `meta_tx_v0` syscall with the captured signature.
3. Submit that outer transaction repeatedly.
4. Each time, the OS computes the same hash, the account contract's `__execute__` verifies the same signature successfully, and the signed operation (e.g., ERC-20 transfer) executes again.

This constitutes **direct, unbounded loss of funds** from the victim account contract.

---

### Likelihood Explanation

- The `meta_tx_v0` syscall is available to any contract without restriction.
- The signature is visible in the transaction trace on-chain.
- No privileged access, leaked key, or operator cooperation is required.
- The attacker only needs to submit a new outer transaction referencing the captured signature.
- Any account contract that uses `meta_tx_v0` for payment or token-transfer authorization is immediately exploitable.

---

### Recommendation

Include a nonce (or a unique, monotonically increasing counter tracked per `contract_address`) in the meta-tx hash, analogous to how regular v1/v3 transactions include the nonce via `additional_data`:

```diff
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
+   nonce: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
-       additional_data_size=0,
-       additional_data=cast(0, felt*),
+       additional_data_size=1,
+       additional_data=&nonce,
    );
    return tx_hash;
}
```

The OS must also track and increment the nonce for the target `contract_address` on each successful `meta_tx_v0` execution, and `check_and_increment_nonce` must not skip version-0 meta transactions.

---

### Proof of Concept

1. Alice's account contract `A` is called via `meta_tx_v0` with `calldata = [transfer, Bob, 100]`. Alice's signature `σ` over `hash(INVOKE_HASH_PREFIX, 0, A, __execute__, calldata, 0, chain_id)` is recorded on-chain.

2. Attacker deploys contract `M`. `M.__execute__` issues:
   ```
   meta_tx_v0(contract_address=A, selector=__execute__, calldata=[transfer, Bob, 100], signature=σ)
   ```

3. Attacker submits an outer invoke transaction calling `M.__execute__`. The OS computes `compute_meta_tx_v0_hash(A, __execute__, calldata, chain_id)` — identical to step 1 — and passes `σ` to `A.__execute__`.

4. `A.__execute__` verifies `σ` against the hash (which matches), executes the transfer, and 100 tokens leave Alice's account.

5. Attacker repeats step 3 until Alice's account is drained. No new signature from Alice is required.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L68-100)
```text
func deprecated_get_transaction_hash{hash_ptr: HashBuiltin*}(
    tx_hash_prefix: felt,
    version: felt,
    contract_address: felt,
    entry_point_selector: felt,
    calldata_size: felt,
    calldata: felt*,
    max_fee: felt,
    chain_id: felt,
    additional_data_size: felt,
    additional_data: felt*,
) -> (tx_hash: felt) {
    let (hash_state_ptr) = hash_init();
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=tx_hash_prefix);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=version);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=contract_address);
    let (hash_state_ptr) = hash_update_single(
        hash_state_ptr=hash_state_ptr, item=entry_point_selector
    );
    let (hash_state_ptr) = hash_update_with_hashchain(
        hash_state_ptr=hash_state_ptr, data_ptr=calldata, data_length=calldata_size
    );
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=max_fee);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=chain_id);

    let (hash_state_ptr) = hash_update(
        hash_state_ptr=hash_state_ptr, data_ptr=additional_data, data_length=additional_data_size
    );

    let (tx_hash) = hash_finalize(hash_state_ptr=hash_state_ptr);

    return (tx_hash=tx_hash);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-315)
```text
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L317-320)
```text
    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-363)
```text
    tempvar new_tx_info = new TxInfo(
        version=0,
        account_contract_address=contract_address,
        max_fee=0,
        signature_start=request.signature_start,
        signature_end=request.signature_end,
        transaction_hash=meta_tx_hash,
        chain_id=old_tx_info.chain_id,
        nonce=0,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-68)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
