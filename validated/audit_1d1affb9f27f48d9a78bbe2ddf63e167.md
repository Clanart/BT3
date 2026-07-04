### Title
`execute_meta_tx_v0` Syscall Does Not Increment Target Contract Nonce, Enabling Unbounded Replay and Unauthorized Fund Transfers - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall hardcodes `nonce=0` for the target contract's execution context and never calls `check_and_increment_nonce` on the target. Because `check_and_increment_nonce` explicitly skips version 0 transactions, and `run_validate` also skips version 0, the target account's nonce is never incremented and its `__validate__` entry point is never invoked. An unprivileged attacker can deploy a malicious contract, call `execute_meta_tx_v0` targeting any victim account with arbitrary calldata, and repeat the call across unlimited outer transactions — draining the victim's funds.

---

### Finding Description

**Root cause — `check_and_increment_nonce` skips version 0:** [1](#0-0) 

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**Root cause — `run_validate` skips version 0:** [2](#0-1) 

```cairo
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```

**Root cause — `execute_meta_tx_v0` hardcodes `version=0` and `nonce=0`, then calls `contract_call_helper` (which never calls `check_and_increment_nonce`):** [3](#0-2) 

```cairo
    tempvar new_tx_info = new TxInfo(
        version=0,
        account_contract_address=contract_address,
        max_fee=0,
        ...
        nonce=0,          // <-- always 0, never incremented
        ...
    );
``` [4](#0-3) 

The call then proceeds to `contract_call_helper`, which executes the target's `__execute__` entry point directly — without ever calling `check_and_increment_nonce` or `run_validate`.

**The meta-transaction hash does not include a nonce:** [5](#0-4) 

`compute_meta_tx_v0_hash` hashes only `contract_address`, `entry_point_selector`, `calldata`, and `chain_id`. With no nonce in the hash and no nonce increment on the target, the same hash is valid forever.

---

### Impact Explanation

Any unprivileged attacker can:

1. Deploy a malicious Sierra contract.
2. From that contract, call `execute_meta_tx_v0` targeting any victim account, supplying arbitrary calldata (e.g., a transfer of all victim funds to the attacker).
3. The victim's `__execute__` runs without `__validate__` being called (no signature check) and without the victim's nonce being incremented.
4. Submit a new outer transaction (with a fresh outer nonce) and repeat step 2 indefinitely.

Because the victim's nonce is never incremented and `__validate__` is never called, there is no on-chain replay protection for the meta transaction. A typical account contract's `__execute__` will execute whatever calls are in the calldata without re-verifying the signature. The attacker can drain the victim's entire balance across successive outer transactions.

**Impact: Critical — Direct loss of funds.**

---

### Likelihood Explanation

The attack requires only:
- A funded StarkNet account (to pay gas for outer transactions).
- Deploying a malicious Sierra contract (standard operation).
- Knowledge of the victim's address and calldata format (both public).

No privileged access, leaked keys, or operator cooperation is needed. Any user on the network can execute this attack.

**Likelihood: Medium** (requires deliberate construction of a malicious contract and multiple transactions, but no special privileges).

---

### Recommendation

1. **Increment the target contract's nonce** inside `execute_meta_tx_v0` after execution, analogous to how `check_and_increment_nonce` works for regular transactions. This prevents replay across outer transactions.
2. **Include the target contract's current nonce** in `compute_meta_tx_v0_hash` so that each meta transaction hash is unique per nonce value.
3. **Call the target contract's `__validate__`** (or enforce that the calling contract has verified the signature before invoking `execute_meta_tx_v0`), so that the target's authentication logic is not bypassed.

---

### Proof of Concept

```
1. Attacker deploys MaliciousRelayer (Sierra contract).

2. MaliciousRelayer.__execute__ contains:
     execute_meta_tx_v0(
         contract_address = VICTIM,
         selector         = EXECUTE_ENTRY_POINT_SELECTOR,
         calldata         = [transfer(ATTACKER, victim_balance)],
         signature        = []   // any value; __validate__ is skipped
     )

3. Attacker submits InvokeTx(nonce=0) → MaliciousRelayer.__execute__
   → victim.__execute__ runs, transfers funds to attacker.
   Victim nonce: still 0.

4. Attacker submits InvokeTx(nonce=1) → MaliciousRelayer.__execute__
   → same meta_tx_v0 hash, victim nonce still 0, victim.__execute__ runs again.

5. Repeat until victim is drained.
```

The meta_tx_v0 hash is identical across all iterations (no nonce in hash). The victim's `__validate__` is never called. The victim's nonce is never incremented. The OS accepts each outer transaction because only the outer account's nonce is checked.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-352)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L389-391)
```text
    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-314)
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
```
