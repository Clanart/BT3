Looking at the scope directory, I need to find an analog to the "missing domain binding in a signed structure" vulnerability class. Let me examine the key file I found.

The critical finding is in `compute_meta_tx_v0_hash` in `transaction_hash.cairo`. Compare it directly with `compute_l1_handler_transaction_hash`:

- L1 handler: `additional_data_size=1, additional_data=&nonce` — nonce is bound into the hash
- meta_tx_v0: `additional_data_size=0, additional_data=cast(0, felt*)` — **no nonce**

This is the direct structural analog to the external report's missing `MarketplaceAddress` field.

---

### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Unbounded Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

### Summary
The `compute_meta_tx_v0_hash` function computes the hash for v0 meta transactions without including any nonce or replay-protection field. Because the hash is fully determined by static inputs `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)`, a valid signature produced once is valid for every future replay of the identical call. An unprivileged attacker who observes a victim's meta_tx_v0 signature on-chain can replay it an unlimited number of times, directly draining the victim's account.

### Finding Description
`compute_meta_tx_v0_hash` (lines 295–315) calls `deprecated_get_transaction_hash` with:

```cairo
additional_data_size=0,
additional_data=cast(0, felt*),
``` [1](#0-0) 

This means the hash commits to no uniqueness identifier. The resulting digest is:

```
H(INVOKE_HASH_PREFIX, 0, contract_address, selector, H(calldata), 0, chain_id)
```

All seven inputs are static for any given logical operation. The signature over this hash is therefore valid for every future invocation of the same call.

Contrast this with `compute_l1_handler_transaction_hash` (lines 220–238), which correctly binds a nonce into the hash via `additional_data_size=1, additional_data=&nonce`: [2](#0-1) 

And with all v3 account transactions, which bind nonce through `hash_tx_common_fields` (line 175): [3](#0-2) 

The `meta_tx_v0` syscall is the only hash-computation path in the OS that omits replay protection entirely.

### Impact Explanation
**Critical — Direct loss of funds.**

A meta_tx_v0 is a mechanism for a contract to execute a call on behalf of an account using a user-provided signature. A typical use is a signed token transfer (approve-and-transfer in one step). Because the hash contains no nonce, the same signature authorises the same transfer forever. An attacker who observes the signature once — from a mempool broadcast, a finalized block, or any public channel — can submit it in back-to-back transactions until the victim's balance is zero. No privileged access is required; the attacker only needs to be a normal transaction sender.

### Likelihood Explanation
**High.** Every meta_tx_v0 signature is necessarily broadcast on-chain (it is part of the transaction calldata). It is therefore visible to every network participant at the moment of first use. The replay requires no special capability beyond submitting a standard transaction. The only limiting factor is the victim's account balance.

### Recommendation
Include a nonce in the `compute_meta_tx_v0_hash` computation, mirroring the pattern already used by `compute_l1_handler_transaction_hash`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
    nonce: felt,          // <-- add nonce parameter
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
        additional_data_size=1,   // <-- was 0
        additional_data=&nonce,   // <-- was cast(0, felt*)
    );
    return tx_hash;
}
```

The nonce must be tracked per-account and incremented by the OS after each successful meta_tx_v0 execution, exactly as account transaction nonces are managed in `check_and_increment_nonce`. [4](#0-3) 

### Proof of Concept

1. **Victim** signs a `meta_tx_v0` authorising `transfer(attacker, 100_STRK)` from their account. The OS computes `H = compute_meta_tx_v0_hash(victim_addr, transfer_selector, [attacker, 100], chain_id)`. The victim submits the transaction; it is included in block N.

2. **Attacker** extracts `(v, r, s)` from the calldata of block N's transaction. Because `compute_meta_tx_v0_hash` produces the identical digest for the identical inputs and no nonce was consumed, the signature is still valid.

3. **Attacker** submits a new transaction in block N+1 carrying the same `meta_tx_v0` syscall with the same `(contract_address, selector, calldata, v, r, s)`. The OS recomputes the same hash, the signature verifies, and another 100 STRK is transferred.

4. Steps 3–4 repeat until the victim's balance reaches zero. Each iteration costs only the attacker's gas fee.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L167-177)
```text
    poseidon_hash_update_single(item=common_fields.tx_hash_prefix);
    poseidon_hash_update_single(item=common_fields.version);
    poseidon_hash_update_single(item=common_fields.sender_address);
    poseidon_hash_update_single(item=fee_fields_hash);
    poseidon_hash_update_with_nested_hash(
        data_ptr=common_fields.paymaster_data, data_length=common_fields.paymaster_data_length
    );
    poseidon_hash_update_single(item=common_fields.chain_id);
    poseidon_hash_update_single(item=common_fields.nonce);
    poseidon_hash_update_single(item=data_availability_modes);

```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-311)
```text
    check_and_increment_nonce(tx_info=tx_info);
```
