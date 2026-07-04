### Title
Meta-transaction v0 hash omits nonce, enabling permanent signature replay — (File: `transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes a Pedersen hash over `(contract_address, selector, calldata, chain_id)` with `additional_data_size=0`, permanently excluding any nonce. The OS then constructs the resulting `TxInfo` with `nonce=0` and explicitly skips nonce enforcement for version-0 transactions. There is no one-time-use lock anywhere in the meta_tx_v0 flow. A valid signature observed in any past block is therefore replayable by any unprivileged actor in any future block, re-executing the same `__execute__` calldata on the target contract without the original signer's consent.

---

### Finding Description

**Root cause — hash omits nonce:**

In `compute_meta_tx_v0_hash`, the call to `deprecated_get_transaction_hash` passes `additional_data_size=0` and `additional_data=cast(0, felt*)`: [1](#0-0) 

This makes the hash a pure deterministic function of `(INVOKE_PREFIX, 0, contract_address, __execute__, H(calldata), 0, chain_id)`. No nonce, no block number, no timestamp is mixed in.

**Root cause — OS sets nonce=0 and skips nonce enforcement:**

`execute_meta_tx_v0` constructs the new `TxInfo` with `version=0` and `nonce=0`: [2](#0-1) 

`check_and_increment_nonce` explicitly returns early for version-0 transactions, so the OS never increments or validates any nonce for this flow: [3](#0-2) 

**Root cause — no one-time-use lock:**

Unlike the withdrawal worker in the external report (which at least attempted a Redis-based reuse lock), the meta_tx_v0 path in the OS has no equivalent mechanism. The syscall dispatcher routes to `execute_meta_tx_v0` unconditionally: [4](#0-3) 

**Analog chain to the external report:**

| External report link | StarkNet OS analog |
|---|---|
| Link A: TOTP code stored raw in job payload | `meta_tx_hash` is deterministic and nonce-free; the "credential" (signature) is permanently valid |
| Link B: same TOTP accepted in export flow | same signature accepted by `execute_meta_tx_v0` in any future block |
| Link C: wide TOTP window (4 steps) | window is infinite — no expiry mechanism exists |
| Link D: no one-time-use lock in export flow | no one-time-use lock anywhere in the meta_tx_v0 OS path |

---

### Impact Explanation

An attacker who observes a meta_tx_v0 call in any past block can extract `(contract_address, calldata, signature)` from public chain data and replay it by deploying a contract that issues the identical `meta_tx_v0` syscall. Because the OS-level hash never changes for the same parameters, the target contract's `__execute__` entry point will accept the replayed signature and re-execute the same calldata — including any token transfers or fund-moving operations — without the original signer's authorisation.

**Impact: Critical — Direct loss of funds.**

---

### Likelihood Explanation

All transaction calldata, including the `signature_start`/`signature_end` fields passed to `meta_tx_v0`, is public on StarkNet. Any unprivileged observer can extract a valid signature from a past block. Replaying it requires only deploying a contract that issues the same syscall — no privileged access, no key material, and no special network position is needed.

**Likelihood: Medium** (requires a prior meta_tx_v0 transaction to exist on-chain for the target contract).

---

### Recommendation

Include a per-use nonce in the meta_tx_v0 hash. The minimal fix is to pass `additional_data_size=1` and `additional_data=&nonce` in `compute_meta_tx_v0_hash`, where `nonce` is sourced from the target contract's on-chain state entry and incremented by the OS after each successful meta_tx_v0 execution — mirroring the existing `check_and_increment_nonce` logic used for regular account transactions. This eliminates the replay surface at the protocol level without requiring contract-level workarounds.

---

### Proof of Concept

1. Alice signs `H(INVOKE_PREFIX, 0, alice_addr, __execute__, H([transfer(bob, 100_ETH)]), 0, chain_id)` → `sig`.
2. A relayer submits a transaction whose contract calls `meta_tx_v0(alice_addr, __execute__, [transfer(bob, 100_ETH)], sig)`. Alice's `__execute__` verifies `sig` against the meta_tx_v0 hash and executes the transfer.
3. Attacker reads `sig` and `calldata` from the public chain.
4. Attacker deploys `AttackerContract` with a single function that calls `meta_tx_v0(alice_addr, __execute__, [transfer(bob, 100_ETH)], sig)`.
5. Because `compute_meta_tx_v0_hash` produces the identical hash (no nonce), Alice's `__execute__` accepts `sig` and executes the transfer again.
6. Attacker repeats step 4 in every subsequent block, draining Alice's account completely.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-68)
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
