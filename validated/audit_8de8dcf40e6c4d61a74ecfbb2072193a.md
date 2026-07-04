### Title
`meta_tx_v0` Signature Hash Omits Nonce, Enabling Unlimited Replay Against Any Target Contract — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` constructs the hash that the target account contract's `__validate__` function verifies, but passes `additional_data_size=0` — omitting any nonce. Because no nonce is committed to in the hash, and `execute_meta_tx_v0` never checks or increments the target contract's nonce, any valid `meta_tx_v0` signature observed on-chain can be replayed an unlimited number of times by any unprivileged party, draining the target contract.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash.cairo` computes:

```
H = Pedersen(INVOKE_HASH_PREFIX, 0, contract_address, selector, calldata_hash, 0, chain_id)
``` [1](#0-0) 

The call to `deprecated_get_transaction_hash` passes `additional_data_size=0` and `additional_data=cast(0, felt*)`. No nonce, block number, or caller address is committed to in the preimage. [2](#0-1) 

In `execute_meta_tx_v0`, the resulting `new_tx_info` is constructed with `nonce=0` hardcoded, and `check_and_increment_nonce` is never called for the target contract: [3](#0-2) 

Compare this to `check_and_increment_nonce`, which explicitly skips version-0 transactions: [4](#0-3) 

The only validation performed before executing the meta-tx is:
1. `selector == EXECUTE_ENTRY_POINT_SELECTOR` — restricts to `__execute__`
2. Signature array length bound check [5](#0-4) 

No caller binding, no nonce, no block binding. The hash `H` is fully determined by `(contract_address, selector, calldata, chain_id)`. Any party who observes a valid `(calldata, signature)` pair for a given target contract can replay it indefinitely.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If a `meta_tx_v0` call transfers tokens or ETH from the target account contract, an attacker who extracts the signature from the original transaction can replay it in a new outer transaction (with a fresh nonce for the attacker's own account). The OS recomputes the identical hash `H` (since no nonce is in the preimage), the target contract's `__validate__` accepts the same signature, and the transfer executes again. This can be repeated until the target contract is drained.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` signature is embedded in the outer transaction's calldata and is publicly visible on-chain the moment the original transaction is included in a block. Any observer can extract it. The attacker only needs to:
1. Deploy a contract that issues the `meta_tx_v0` syscall with the stolen `(contract_address, selector, calldata, signature)`.
2. Submit a new outer transaction calling that contract.

No privileged access, leaked key, or special role is required. The `meta_tx_v0` syscall is available to any Cairo 1 contract during execution. [6](#0-5) 

---

### Recommendation

Include a nonce in the `meta_tx_v0` hash preimage. The `MetaTxV0Request` struct should carry a caller-supplied nonce, and `execute_meta_tx_v0` should verify and increment the target contract's nonce (or a dedicated meta-tx nonce slot) before executing. Concretely, pass the nonce as `additional_data` in `compute_meta_tx_v0_hash`:

```cairo
additional_data_size=1,
additional_data=&nonce,
```

and enforce `check_and_increment_nonce` for the target contract regardless of version.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. Victim contract `V` (a v0-compatible account) receives a legitimate `meta_tx_v0` call from contract `C` with `calldata = [transfer(attacker, 1 ETH)]` and signature `S`. The OS computes `H = hash(INVOKE_PREFIX, 0, V, __execute__, calldata, 0, chain_id)` and `V.__validate__` accepts `S`.

2. Attacker deploys malicious contract `M` with the following logic in its `__execute__`:
   ```
   syscall: meta_tx_v0(contract_address=V, selector=__execute__, calldata=same_calldata, signature=S)
   ```

3. Attacker submits an invoke transaction calling `M.__execute__` (with attacker's own nonce, so no replay issue for the outer tx).

4. The OS executes `execute_meta_tx_v0`:
   - Computes `H' = hash(INVOKE_PREFIX, 0, V, __execute__, same_calldata, 0, chain_id)` — identical to `H` since no nonce is in the preimage.
   - Passes `S` as the signature to `V.__validate__`.
   - `V.__validate__` verifies `S` against `H'` — succeeds.
   - `V.__execute__` runs, transferring 1 ETH to attacker again.

5. Step 3–4 can be repeated indefinitely until `V` is drained. [1](#0-0) [7](#0-6) [3](#0-2)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L317-323)
```text
    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    // Sanity check: Verify that `signature` is a valid Sierra array.
    assert_nn_le(request.signature_end - request.signature_start, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-340)
```text
    // Compute the meta-transaction hash.
    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let meta_tx_hash = compute_meta_tx_v0_hash(
            contract_address=contract_address,
            entry_point_selector=selector,
            calldata=calldata_start,
            calldata_size=calldata_size,
            chain_id=old_tx_info.chain_id,
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-350)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
    %{ OsLoggerExitSyscall %}
    return execute_syscalls(
        block_context=block_context,
        execution_context=execution_context,
        syscall_ptr_end=syscall_ptr_end,
    );
```
