### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Unbounded Signature Replay via `meta_tx_v0` Syscall - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function omits a nonce from the transaction hash commitment. Combined with the OS explicitly skipping nonce checks for version-0 transactions, any valid `meta_tx_v0` signature is permanently and unconditionally replayable by any unprivileged caller. This is the direct StarkNet-protocol analog of the reported "missing `msg.sender` in signature" class: a critical binding field is absent from the signed commitment, allowing the same authorization to be re-used across unlimited executions.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash.cairo` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

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
        additional_data=cast(0, felt*),   // ← no nonce
    );
``` [1](#0-0) 

The resulting hash is a pure function of `(INVOKE_HASH_PREFIX, 0, contract_address, entry_point_selector, calldata_hash, 0, chain_id)`. No nonce, no block number, no caller address is committed.

In `execute_meta_tx_v0`, the OS constructs a `TxInfo` with `nonce=0` and never calls `check_and_increment_nonce` for it:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,          // ← hardcoded zero, never checked or incremented
    ...
);
``` [2](#0-1) 

This is consistent with `check_and_increment_nonce`, which explicitly bails out for version-0 transactions:

```cairo
func check_and_increment_nonce{...}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

The `execute_meta_tx_v0` function is reachable from any non-virtual-OS invoke transaction via the `META_TX_V0_SELECTOR` syscall dispatch:

```cairo
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A standard StarkNet account's `__validate__` entry point verifies the caller's signature against `tx_info.transaction_hash`. Because `compute_meta_tx_v0_hash` produces an identical hash for identical `(contract_address, selector, calldata, chain_id)` inputs on every invocation, a signature that was valid once is valid forever for those same inputs.

An attacker who observes (on-chain or off-chain) a legitimate `meta_tx_v0` call — e.g., one that authorized a token transfer from victim account `A` — can replay it in any subsequent block by deploying a contract that issues the same `meta_tx_v0` syscall with the same `contract_address`, `selector`, `calldata`, and `signature`. The OS will compute the same hash, the victim account's `__validate__` will accept the same signature, and `__execute__` will run again, draining funds repeatedly until the account is empty. [5](#0-4) 

---

### Likelihood Explanation

**High.** The attacker entry path requires only:
1. Observing one valid `meta_tx_v0` invocation (publicly visible on-chain).
2. Deploying a contract that re-issues the same syscall with the captured parameters.
3. Submitting an invoke transaction calling that contract.

No privileged access, no leaked keys, no operator cooperation is required. Any unprivileged L2 transaction sender can execute this attack.

---

### Recommendation

Include a nonce in the `compute_meta_tx_v0_hash` commitment, passed as `additional_data`, and enforce that the nonce is checked and incremented in the target account's state — analogous to how `check_and_increment_nonce` works for version-3 transactions. The nonce must be bound to `contract_address` (the meta-tx target), not the outer caller. Alternatively, include the current block number or a monotonic counter committed to the target account's storage to prevent replay across blocks. [6](#0-5) 

---

### Proof of Concept

1. Victim account `V` at address `0xVVVV` holds 1000 STRK. A relayer legitimately submits a `meta_tx_v0` syscall with:
   - `contract_address = 0xVVVV`
   - `selector = __execute__`
   - `calldata = [transfer(attacker, 100)]`
   - `signature = sig_V` (victim's valid ECDSA signature over the hash)

2. The OS computes `H = hash(INVOKE_PREFIX, 0, 0xVVVV, __execute__, calldata_hash, 0, chain_id)`. Victim's `__validate__` accepts `sig_V` for `H`. Transfer executes.

3. Attacker deploys contract `Evil` with a function that calls `meta_tx_v0(contract_address=0xVVVV, selector=__execute__, calldata=[transfer(attacker,100)], signature=sig_V)`.

4. Attacker invokes `Evil` in block N+1. The OS recomputes the same `H` (no nonce in hash, no nonce check for version 0). Victim's `__validate__` accepts `sig_V` again. Another 100 STRK transferred.

5. Attacker repeats step 4 nine more times across subsequent blocks, draining all 1000 STRK from `V`. [7](#0-6) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```
