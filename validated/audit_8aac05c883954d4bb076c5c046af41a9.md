### Title
Meta-Transaction v0 Hash Missing Nonce Enables Within-Chain Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes the hash used to authenticate a `meta_tx_v0` syscall without including any nonce or deadline. Because the hash commits only to `(prefix, version, contract_address, selector, calldata, max_fee=0, chain_id)` and passes `additional_data_size=0` / `additional_data=cast(0, felt*)`, the OS provides no within-chain replay protection for meta-transactions. An attacker who observes a valid `(calldata, signature)` pair from a prior `meta_tx_v0` call can replay it an unlimited number of times against the same account contract, causing repeated unauthorized execution of the same action (e.g., repeated fund transfers).

---

### Finding Description

**Root cause — `compute_meta_tx_v0_hash`:** [1](#0-0) 

The function signature accepts `chain_id` but passes `additional_data_size=0` and `additional_data=cast(0, felt*)` to `deprecated_get_transaction_hash`. No nonce, counter, or deadline is committed to in the hash. The hash is therefore fully determined by `(contract_address, selector, calldata, chain_id)` — all of which are static for a given intended action.

**Nonce is explicitly skipped for version 0:** [2](#0-1) 

`check_and_increment_nonce` returns immediately when `tx_info.version == 0`, which is exactly the version assigned to every meta-transaction.

**`execute_meta_tx_v0` hardcodes `nonce=0` in the synthesized `TxInfo`:** [3](#0-2) 

The `nonce=0` field is hardcoded. No per-invocation counter is tracked or enforced by the OS.

**`__validate__` is also skipped for version 0:** [4](#0-3) 

`run_validate` returns immediately for version-0 transactions, so the account contract's `__validate__` entry point is never invoked. The only signature check is whatever the account contract's `__execute__` performs internally against `tx_info.transaction_hash`.

**`execute_meta_tx_v0` calls `__execute__` directly:** [5](#0-4) 

`contract_call_helper` is invoked directly with the synthesized execution context, bypassing all nonce and validation checks.

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

A signed `meta_tx_v0` message is bound only to `(contract_address, selector, calldata, chain_id)`. Once a valid `(calldata, signature)` pair is observed on-chain (e.g., a transfer of ERC-20 tokens from a victim account), any unprivileged actor can deploy a contract that calls the `meta_tx_v0` syscall with the identical parameters. The OS will compute the identical hash, the account contract's `__execute__` will receive the identical `(transaction_hash, signature)` pair, and — if the account contract does not implement its own application-level nonce — will execute the transfer again. This can be repeated indefinitely, draining the victim account.

---

### Likelihood Explanation

**Likelihood: High.**

- The `meta_tx_v0` syscall is callable by any unprivileged contract deployed on StarkNet.
- All inputs needed for replay (`contract_address`, `selector`, `calldata`, `signature`) are visible on-chain after the first execution.
- Legacy v0 account contracts (the intended users of this syscall, given the version-0 hash format) are precisely the contracts least likely to implement application-level nonce protection in `__execute__`, since the original v0 protocol relied on the OS-level nonce mechanism — which is explicitly disabled here.
- No privileged access, key compromise, or network-level attack is required.

---

### Recommendation

Include a replay-prevention field in the `compute_meta_tx_v0_hash` computation. Two options:

1. **Nonce**: Track a per-`(caller_contract, target_contract)` or per-`target_contract` nonce in contract storage and commit it to the hash via `additional_data`. Increment it on each successful `meta_tx_v0` execution.
2. **Deadline**: Require the caller to supply a block-number or timestamp deadline and commit it to the hash. This mirrors the fix applied to `authorizedSignerProof` in the referenced TAP contracts PR.

Concretely, change `compute_meta_tx_v0_hash` to pass a nonce or deadline through `additional_data_size` / `additional_data` in the call to `deprecated_get_transaction_hash`, and enforce its uniqueness or expiry in `execute_meta_tx_v0`.

---

### Proof of Concept

1. Alice's account contract (`0xALICE`) accepts v0 meta-transactions. Bob's relayer contract calls `meta_tx_v0` with:
   - `contract_address = 0xALICE`
   - `selector = EXECUTE_ENTRY_POINT_SELECTOR`
   - `calldata = [transfer(recipient=0xBOB, amount=100)]`
   - `signature = alice_sig` (Alice's ECDSA signature over the Pedersen hash of the above + `chain_id`)

2. The OS computes:
   ```
   meta_tx_hash = Pedersen(INVOKE_PREFIX, 0, 0xALICE, EXECUTE_SELECTOR,
                            hash(calldata), 0, chain_id)
   ```
   No nonce is included. `alice_sig` is valid for this hash.

3. Alice's `__execute__` runs, transfers 100 tokens to Bob. The call succeeds.

4. Attacker deploys `ReplayContract` which calls `meta_tx_v0` with the identical `(0xALICE, EXECUTE_SELECTOR, calldata, alice_sig)` — all values are public on-chain.

5. The OS computes the **identical** `meta_tx_hash` (same inputs, no nonce). Alice's `__execute__` receives the same `(transaction_hash, signature)` and executes the transfer again.

6. Step 4–5 can be repeated in every subsequent block until Alice's balance is zero.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L389-393)
```text
    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );
```
