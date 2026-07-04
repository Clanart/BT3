### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Unlimited Signature Replay - (File: `transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` omits the nonce from the hash, and `execute_meta_tx_v0` hardcodes `nonce=0` with no nonce check or increment. Any attacker who observes a valid meta-transaction signature can replay it an unlimited number of times in fresh outer transactions, leading to direct loss of funds from the victim's account.

---

### Finding Description

**Root cause — hash omits nonce:**

In `transaction_hash.cairo`, `compute_meta_tx_v0_hash` calls `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`: [1](#0-0) 

The hash therefore commits only to `(INVOKE_HASH_PREFIX, version=0, contract_address, entry_point_selector, calldata, max_fee=0, chain_id)` — **no nonce**.

Compare this to `compute_l1_handler_transaction_hash`, which explicitly passes `additional_data_size=1, additional_data=&nonce`: [2](#0-1) 

**Root cause — OS never checks or increments a nonce for meta_tx_v0:**

In `syscall_impls.cairo`, `execute_meta_tx_v0` constructs the new `TxInfo` with `nonce=0` hardcoded and never calls `check_and_increment_nonce`: [3](#0-2) 

The OS therefore provides **zero replay protection** for `meta_tx_v0` calls. Because the hash only commits to `(contract_address, selector, calldata, chain_id)`, any attacker who obtains a valid `(signature, calldata)` pair can replay it in a fresh outer transaction by deploying or calling any contract that issues the `meta_tx_v0` syscall with those exact parameters.

The syscall is dispatched from `execute_syscalls.cairo`: [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A victim who authorizes a `meta_tx_v0` call (e.g., a token transfer via their v0-style account's `__execute__` entry point) produces a signature that is permanently valid. An attacker can replay it an unlimited number of times in new outer transactions, draining the victim's account completely. The OS never increments or checks any nonce for `meta_tx_v0`, so each replay succeeds identically to the first.

---

### Likelihood Explanation

**High.** The attacker requires no privileged access. They only need to observe a single valid `meta_tx_v0` signature from the public mempool or on-chain history, then submit new outer transactions that replay it. The outer transaction's own nonce is irrelevant — it protects the outer transaction from replay, not the embedded `meta_tx_v0` signature.

---

### Recommendation

Include a nonce in `compute_meta_tx_v0_hash` (analogous to `compute_l1_handler_transaction_hash`), passing it as `additional_data_size=1, additional_data=&nonce`. Add a corresponding nonce source and `check_and_increment_nonce` call inside `execute_meta_tx_v0` so the OS enforces single-use semantics for each meta-transaction signature.

---

### Proof of Concept

1. Victim's v0-style account contract signs a `meta_tx_v0` message covering `(contract_address=victim_account, selector=__execute__, calldata=[transfer_to_attacker, amount], chain_id)`. No nonce is committed to.
2. Victim submits an outer invoke transaction to a relayer contract, passing the signature in calldata.
3. The relayer contract issues `meta_tx_v0(contract_address=victim_account, selector=__execute__, calldata=..., signature=victim_sig)`. The transfer executes.
4. Attacker observes the outer transaction in the mempool, extracts `victim_sig` and the calldata.
5. Attacker deploys a contract that issues `meta_tx_v0(contract_address=victim_account, selector=__execute__, calldata=same_calldata, signature=victim_sig)`.
6. Attacker submits repeated outer transactions (each with a fresh nonce for the outer tx) calling their contract.
7. Each replay executes the identical transfer. The OS never checks or increments any nonce for `meta_tx_v0`, so every replay succeeds, draining the victim's funds.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L220-238)
```text
func compute_l1_handler_transaction_hash{pedersen_ptr: HashBuiltin*}(
    execution_context: ExecutionContext*, chain_id: felt, nonce: felt
) -> felt {
    let (__fp__, _) = get_fp_and_pc();
    let (transaction_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=L1_HANDLER_HASH_PREFIX,
        version=L1_HANDLER_VERSION,
        contract_address=execution_context.execution_info.contract_address,
        entry_point_selector=execution_context.execution_info.selector,
        calldata_size=execution_context.calldata_size,
        calldata=execution_context.calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=1,
        additional_data=&nonce,
    );

    return transaction_hash;
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
