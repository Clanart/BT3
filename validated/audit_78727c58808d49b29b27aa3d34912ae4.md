### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function omits a nonce from the transaction hash, and `execute_meta_tx_v0` never calls `check_and_increment_nonce`. Any valid meta-tx v0 signature can be replayed an unlimited number of times by an unprivileged caller, leading to direct loss of funds.

---

### Finding Description

The `meta_tx_v0` syscall is designed to let a contract invoke another contract using a version-0 transaction hash and a caller-supplied signature. The hash is computed in `compute_meta_tx_v0_hash`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        chain_id=chain_id,
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
``` [1](#0-0) 

`additional_data_size=0` means no nonce is mixed into the hash. Compare this to `compute_l1_handler_transaction_hash`, which explicitly passes `additional_data_size=1, additional_data=&nonce` for replay protection: [2](#0-1) 

In `execute_meta_tx_v0`, the constructed `new_tx_info` hard-codes `nonce=0` and no `check_and_increment_nonce` is ever called:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,          // ← always zero, never incremented
    ...
);
``` [3](#0-2) 

The chain_id used in the hash is inherited from the outer transaction's `old_tx_info.chain_id`: [4](#0-3) 

Because the hash is a pure function of `(contract_address, selector, calldata, chain_id)` with no nonce, the same hash — and therefore the same signature — is valid for every invocation.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If a user signs a meta-tx v0 authorizing a token transfer (or any state-mutating call), an attacker who observes that signature on-chain can replay the identical syscall call repeatedly. Each replay produces the same hash, passes the account contract's `__validate__` (which sees the same valid signature against the same hash), and executes the same transfer. The user's balance is drained until it reaches zero.

---

### Likelihood Explanation

**Medium.** The `meta_tx_v0` syscall is callable by any deployed contract without any privileged role. Signatures submitted on-chain are publicly visible. An attacker only needs to:
1. Observe a meta-tx v0 syscall in a transaction.
2. Extract the signature from the syscall request.
3. Construct a new transaction that calls the same contract with the same calldata and the captured signature.

No key compromise, Sybil attack, or operator collusion is required.

---

### Recommendation

1. **Short term**: Include a nonce in `compute_meta_tx_v0_hash` by passing `additional_data_size=1` and a nonce value as `additional_data`, mirroring the pattern used in `compute_l1_handler_transaction_hash`. Call `check_and_increment_nonce` inside `execute_meta_tx_v0` after the hash is verified.

2. **Long term**: Audit all hash-computation paths to ensure every user-signed hash includes a monotonically increasing nonce or other one-time binding, and add a static assertion or comment documenting the replay-protection strategy for each transaction type.

---

### Proof of Concept

1. Alice signs a meta-tx v0 payload: `H = Pedersen(INVOKE_PREFIX, 0, alice_account, __execute__, calldata_hash, 0, chain_id)` authorizing a 100-token transfer to Bob.
2. Alice submits a transaction that calls a contract C, which issues the `meta_tx_v0` syscall with Alice's signature.
3. The OS computes the same `H`, presents it to Alice's account `__validate__`, which approves the valid signature, and executes the transfer. Alice loses 100 tokens.
4. Attacker Eve observes Alice's signature. Eve submits a new transaction calling contract C (or any contract) with the same `meta_tx_v0` syscall parameters and Alice's signature.
5. The OS recomputes the identical `H` (same inputs, no nonce), Alice's `__validate__` approves again, and another 100 tokens are transferred.
6. Eve repeats step 4–5 until Alice's balance is zero.

The root cause is in:
- `compute_meta_tx_v0_hash` — `additional_data_size=0` omits the nonce. [5](#0-4) 
- `execute_meta_tx_v0` — `nonce=0` is hard-coded and never incremented. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-339)
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
