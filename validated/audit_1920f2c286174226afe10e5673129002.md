### Title
Signature Replay in `execute_meta_tx_v0` Due to Missing Nonce in Hash and No Nonce Increment for Version-0 — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall in the StarkNet OS allows any contract to execute a version-0 meta-transaction on behalf of any target account by supplying a signature. Because the meta-tx hash does not include a nonce and the OS explicitly skips nonce checking and incrementing for version-0 transactions, the same signature is permanently valid and can be replayed by any unprivileged caller an unlimited number of times, enabling direct theft of funds from victim accounts.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` is a syscall available to any executing contract. It accepts a caller-supplied `contract_address`, `calldata`, and `signature`, computes a version-0 transaction hash, and invokes the `__execute__` entry point of the target account with that signature.

**Root cause 1 — No nonce in the meta-tx hash.**

`compute_meta_tx_v0_hash` in `transaction_hash.cairo` calls `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

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
        additional_data_size=0,
        additional_data=cast(0, felt*),   // ← no nonce committed
    );
``` [1](#0-0) 

**Root cause 2 — Nonce check is explicitly skipped for version 0.**

`check_and_increment_nonce` in `execute_transaction_utils.cairo` returns immediately when `tx_info.version == 0`:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [2](#0-1) 

**Root cause 3 — `execute_meta_tx_v0` sets `version=0` and `nonce=0` and never calls `check_and_increment_nonce`.**

The new `TxInfo` constructed inside `execute_meta_tx_v0` hard-codes `version=0` and `nonce=0`, and no nonce check or increment is performed anywhere in the function:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [3](#0-2) 

The syscall is reachable by any contract via the normal syscall dispatch path:

```cairo
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A victim's meta-tx v0 signature commits only to `(contract_address, selector, calldata, chain_id)`. Because no nonce is committed and no nonce is incremented on use, the signature is permanently valid. Any attacker who observes the signature (from a pending transaction in the mempool, from a previously included block, or from any off-chain channel) can replay it an unlimited number of times by deploying a contract that calls `execute_meta_tx_v0` with the same parameters. Each replay executes `__execute__` on the victim's account with the victim's signature, allowing the attacker to drain the victim's token balances or perform any other action the original calldata encodes.

---

### Likelihood Explanation

**High.** The `execute_meta_tx_v0` syscall is available to every contract without restriction. Once a meta-tx v0 signature is broadcast (e.g., in a relayer flow), it is observable by any network participant. The replay requires only deploying a contract and submitting a standard invoke transaction — no privileged access, no leaked keys, and no external dependency is needed. The attack is deterministic and repeatable.

---

### Recommendation

1. **Include a nonce in the meta-tx v0 hash.** Extend `compute_meta_tx_v0_hash` to accept and commit a nonce in `additional_data`, analogous to how regular v0 transactions include a nonce in `additional_data`.

2. **Check and increment the target contract's nonce inside `execute_meta_tx_v0`.** After computing the hash and before executing the entry point, read the target contract's current nonce from `contract_state_changes`, assert it matches the nonce in the request, and write back an incremented nonce — regardless of the transaction version being 0.

3. **Alternatively, restrict `execute_meta_tx_v0` to be callable only from a designated relayer contract** whose own nonce provides replay protection, so that each outer transaction can only carry one meta-tx v0 execution.

---

### Proof of Concept

1. **Victim** signs a meta-tx v0 authorizing `calldata = [transfer(attacker, 1000_tokens)]` on their account contract `A`. A relayer broadcasts an outer invoke transaction containing this signature.

2. **Attacker** observes the signature `(A, __execute__, calldata, sig)` from the mempool.

3. **Attacker** deploys a malicious contract `M` whose `__execute__` calls:
   ```
   meta_tx_v0(contract_address=A, selector=__execute__, calldata=calldata, signature=sig)
   ```

4. Attacker submits an outer invoke transaction calling `M.__execute__`. The OS dispatches `execute_meta_tx_v0`:
   - Computes `hash(INVOKE_PREFIX, 0, A, __execute__, calldata, 0, chain_id)` — identical to the original hash.
   - Sets `new_tx_info.version = 0`, `new_tx_info.nonce = 0`.
   - Calls `A.__execute__` with the victim's signature. The account's `__validate__` passes because the hash matches.
   - **No nonce is incremented** on `A`.

5. The attacker repeats step 4 in every subsequent block. Each time, the hash is identical, the signature is valid, and the victim's account executes the transfer — draining funds until the account is empty.

6. When the victim's original relayer transaction is eventually included, it also succeeds (the nonce was never consumed), but the funds are already gone.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```
