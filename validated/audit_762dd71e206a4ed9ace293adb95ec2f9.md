### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Permanent Replay of Signed Meta-Transactions — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` omits any nonce or sequence number from the signed commitment. Combined with the OS explicitly skipping nonce enforcement for version-0 transactions, a captured meta-transaction signature is permanently valid and can be replayed by any unprivileged caller to re-execute arbitrary calldata on behalf of the victim account, leading to direct loss of funds.

---

### Finding Description

`compute_meta_tx_v0_hash` constructs the hash that the target account's `__execute__` entry point will verify against the provided signature:

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
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no deadline
    );
    return tx_hash;
}
``` [1](#0-0) 

The hash covers only `(INVOKE_HASH_PREFIX, version=0, contract_address, selector, calldata_hash, max_fee=0, chain_id)`. No nonce, block number, or deadline is committed to.

In `execute_meta_tx_v0`, the synthesised `TxInfo` hard-codes `nonce=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,   // ← always zero
    ...
);
``` [2](#0-1) 

The OS nonce-enforcement function explicitly skips version-0 transactions:

```cairo
func check_and_increment_nonce(...) {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [3](#0-2) 

Similarly, `run_validate` is skipped for version-0 transactions:

```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [4](#0-3) 

The `meta_tx_v0` syscall is reachable by any contract via the standard syscall dispatch:

```cairo
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

Because the meta-transaction hash commits to no nonce, the same `(contract_address, selector, calldata, chain_id)` tuple always produces the same hash. Any account contract whose `__execute__` verifies the signature against this hash (the standard pattern) will accept the same signature on every future replay. An attacker who captures a valid signature — from the mempool, from on-chain calldata, or from a prior block — can replay the meta-transaction an unlimited number of times in subsequent blocks, draining the victim account of any asset the original calldata transferred.

---

### Likelihood Explanation

**Medium.** The `meta_tx_v0` syscall is callable by any unprivileged contract. Signatures are broadcast in transaction calldata and are permanently visible on-chain. Any account that has ever legitimately executed a meta_tx_v0 token transfer has exposed a replayable signature. No privileged access, key compromise, or network-level attack is required — only the ability to submit a version-3 transaction that calls a contract invoking the syscall.

---

### Recommendation

Include a per-account nonce (or a monotonically increasing sequence number scoped to the target `contract_address`) in the `compute_meta_tx_v0_hash` inputs, analogous to how `additional_data` carries the nonce in `compute_l1_handler_transaction_hash`:

```cairo
// current (vulnerable)
additional_data_size=0,
additional_data=cast(0, felt*),

// recommended
additional_data_size=1,
additional_data=&nonce,   // nonce read from contract_state_changes[contract_address]
```

The OS must then increment the target contract's nonce after a successful meta_tx_v0 execution, mirroring `check_and_increment_nonce` for version ≥ 1 transactions. [6](#0-5) 

---

### Proof of Concept

1. **Alice** legitimately signs a meta_tx_v0 for `transfer(bob, 1000_STRK)` on token contract `T`. The OS computes:
   ```
   meta_hash = H(INVOKE_HASH_PREFIX, 0, alice, __execute__, calldata, 0, chain_id)
   ```
   Alice's account verifies `sig` against `meta_hash` and approves. The transfer executes in block N.

2. **Attacker** observes `(calldata, sig)` in block N's transaction data (publicly visible on-chain).

3. Attacker deploys contract `Evil` containing:
   ```cairo
   // inside __execute__:
   meta_tx_v0(contract_address=alice, selector=__execute__, calldata=calldata, signature=sig)
   ```

4. Attacker submits a version-3 invoke transaction (with their own nonce) calling `Evil.__execute__` in block N+1.

5. The OS calls `execute_meta_tx_v0`:
   - Recomputes `meta_hash` — identical to step 1 (no nonce in hash).
   - Sets `new_tx_info.nonce = 0`, skips `check_and_increment_nonce`.
   - Calls Alice's `__execute__` with `transaction_hash = meta_hash` and `signature = sig`.

6. Alice's account verifies `sig` against `meta_hash` — **it matches** — and executes `transfer(bob, 1000_STRK)` again.

7. Attacker repeats steps 4–6 in every subsequent block until Alice's balance is zero. [7](#0-6) [8](#0-7)

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
