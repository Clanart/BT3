### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Unbounded Signature Replay for Meta Transactions - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function computes the hash for a `meta_tx_v0` syscall without including any nonce. Combined with `execute_meta_tx_v0` hardcoding `nonce=0` and the OS explicitly skipping nonce enforcement for version-0 transactions, a valid user signature for a `meta_tx_v0` call can be replayed an unlimited number of times by any unprivileged attacker, leading to direct loss of funds.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash/transaction_hash.cairo` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`: [1](#0-0) 

The resulting hash covers only: `tx_hash_prefix || version=0 || contract_address || entry_point_selector || H(calldata) || max_fee=0 || chain_id`. **No nonce is included.**

In `execute_meta_tx_v0` (`syscall_impls.cairo`), the `TxInfo` constructed for the meta-transaction hardcodes `nonce=0`: [2](#0-1) 

`check_and_increment_nonce` in `execute_transaction_utils.cairo` explicitly returns early for version-0 transactions without any nonce check or increment: [3](#0-2) 

The OS then calls `__execute__` on the target account contract directly (bypassing `__validate__`) via `contract_call_helper`, passing the caller-supplied signature and the nonce-free hash as the authoritative `tx_info`: [4](#0-3) 

Because the hash is a pure function of `(contract_address, selector, calldata, chain_id)` with no nonce, the hash — and therefore the signature's validity — is **identical across every replay**.

---

### Impact Explanation

An account contract's `__execute__` entry point that verifies the caller-supplied signature against `tx_info.transaction_hash` (the standard pattern for meta-transaction account implementations) will accept the replayed signature as valid on every invocation. If the authorized calldata encodes a token transfer or any other asset-moving operation, an attacker can drain the victim's account by replaying the same signature repeatedly. This constitutes **direct, critical loss of funds**.

---

### Likelihood Explanation

The attack requires only:
1. Observing one on-chain `meta_tx_v0` call that carried a valid user signature (trivially available from transaction history).
2. Deploying a contract that issues a `meta_tx_v0` syscall with the same `(contract_address, selector, calldata, signature)` tuple.

No privileged access, leaked keys, or off-chain coordination is needed. The attacker controls all inputs and the entry path is fully reachable by any unprivileged transaction sender.

---

### Recommendation

Include a per-account nonce in `compute_meta_tx_v0_hash` — analogous to how `deprecated_get_transaction_hash` accepts `additional_data` for the nonce in L1-handler hashes: [5](#0-4) 

Specifically:
- Read the current nonce of `contract_address` from `contract_state_changes` inside `execute_meta_tx_v0`.
- Pass it as `additional_data` (size 1) to `deprecated_get_transaction_hash` inside `compute_meta_tx_v0_hash`.
- Increment the nonce in `contract_state_changes` after a successful call (do not rely on `check_and_increment_nonce`, which unconditionally skips version-0).

---

### Proof of Concept

1. **Alice** signs a `meta_tx_v0` message authorizing a token transfer from her account: `H(INVOKE_PREFIX || 0 || alice_addr || __execute__ || H(transfer_calldata) || 0 || chain_id)`. She submits it through a relayer contract.

2. **Bob** observes the on-chain transaction and extracts `(alice_addr, __execute__, transfer_calldata, alice_signature)`.

3. Bob deploys `ReplayContract` with the following logic:
   ```
   fn replay() {
       syscall meta_tx_v0(
           contract_address = alice_addr,
           selector         = __execute__,
           calldata         = transfer_calldata,   // same as step 1
           signature        = alice_signature,     // same as step 1
       );
   }
   ```

4. Bob calls `ReplayContract.replay()` in a new transaction. The OS computes `compute_meta_tx_v0_hash(alice_addr, __execute__, transfer_calldata, chain_id)` — **identical to step 1** because no nonce is mixed in.

5. Alice's account contract's `__execute__` receives `tx_info.transaction_hash = original_hash` and `tx_info.signature = alice_signature`. Signature verification passes.

6. The token transfer executes again. Bob repeats steps 3–6 until Alice's balance is zero.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L389-399)
```text
    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );

    // Entries before this point belong to the callee.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```
