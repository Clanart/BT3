### Title
Missing Nonce in `meta_tx_v0` Hash Enables Signature Replay — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes the signed digest for the `meta_tx_v0` syscall without including any nonce or replay-protection field. Because the OS-level nonce check is also explicitly skipped for version-0 transactions, a valid meta-transaction signature observed on-chain can be replayed an unlimited number of times against the same contract, causing repeated execution of the signed call — including fund-draining operations.

---

### Finding Description

`compute_meta_tx_v0_hash` builds the hash that a user must sign to authorize a v0 meta transaction: [1](#0-0) 

```cairo
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
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no nonce
    );
    return tx_hash;
}
```

The signed domain is: `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)`. **No nonce or sequence number is committed to.**

Contrast this with `compute_l1_handler_transaction_hash`, which passes `additional_data_size=1, additional_data=&nonce` to bind the hash to a single use: [2](#0-1) 

The OS-level nonce enforcement (`check_and_increment_nonce`) explicitly returns early for version 0, providing no compensating control: [3](#0-2) 

```cairo
func check_and_increment_nonce{...}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

The result: once a user's meta-transaction signature is broadcast and included in a block, the `(contract_address, selector, calldata)` tuple is permanently valid. Any party that observes the signature can re-submit the identical meta transaction and the OS will accept it, because the computed hash is identical and the signature still verifies.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If the signed calldata encodes a token transfer, an ERC-20 `approve`, or any fund-moving operation, an attacker who copies the signature from a finalized block can replay it repeatedly until the account is drained. The OS provides no mechanism to reject the replayed transaction: the hash matches, the signature is valid, and no nonce is checked.

---

### Likelihood Explanation

**High.** All transaction data, including signatures, is publicly visible on-chain after the first inclusion. No privileged access is required — any observer of the network can extract the signature and re-submit the meta transaction. The attacker's only constraint is paying the gas for the outer wrapping transaction.

---

### Recommendation

Include a per-account, monotonically incrementing nonce in the `compute_meta_tx_v0_hash` digest, mirroring the pattern used by `compute_l1_handler_transaction_hash`:

```cairo
additional_data_size=1,
additional_data=&nonce,
```

Correspondingly, `check_and_increment_nonce` must be extended to enforce the nonce for meta-tx-v0 calls rather than returning early for `version == 0`.

---

### Proof of Concept

1. User signs a meta transaction: `sig = sign(H(INVOKE_PREFIX, 0, contract_addr, selector, calldata, 0, chain_id))`.
2. Backend submits the transaction; it is executed and included in block N.
3. Attacker reads `sig` from block N's transaction data.
4. Attacker submits a new outer transaction that invokes the same contract with the same `(selector, calldata, sig)`.
5. The OS calls `compute_meta_tx_v0_hash` → produces the identical hash → signature verification passes → the meta transaction executes again.
6. Steps 4–5 repeat until the target account is drained.

No privileged role, leaked key, or network-level attack is required. The only input is a publicly visible on-chain signature.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L294-315)
```text
// Computes the hash of a v0 meta transaction. See the `meta_tx_v0` syscall.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```
