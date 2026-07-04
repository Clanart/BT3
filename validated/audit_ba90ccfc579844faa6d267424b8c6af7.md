### Title
Unauthorized `__execute__` Invocation via `execute_meta_tx_v0` Syscall Bypasses Signature Verification — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall implementation in the StarkNet OS allows **any executing contract** to invoke the `__execute__` entry point of **any arbitrary target contract** without the OS performing signature verification or running `__validate__`. The `contract_address` field in the syscall request is fully attacker-controlled, and no authorization check is performed to confirm the caller is permitted to act on behalf of the target. This is a direct analog to the reported `redeem(owner)` vulnerability: just as any caller could supply an arbitrary `owner` to drain allowances, any contract can supply an arbitrary `contract_address` to trigger `__execute__` on a victim account.

---

### Finding Description

`execute_meta_tx_v0` is a syscall available to all executing contracts, dispatched unconditionally in `execute_syscalls.cairo`:

```
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [1](#0-0) 

Inside `execute_meta_tx_v0`, the target `contract_address` is read directly from the attacker-supplied request with no authorization check:

```cairo
local contract_address = request.contract_address;
``` [2](#0-1) 

The function then constructs a synthetic `TxInfo` with `version=0`, `nonce=0`, and the attacker-supplied `signature`, setting `account_contract_address` to the victim:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    ...
    signature_start=request.signature_start,
    signature_end=request.signature_end,
    transaction_hash=meta_tx_hash,
    nonce=0,
    ...
);
``` [3](#0-2) 

It then calls `contract_call_helper` → `select_execute_entry_point_func`, directly executing `__execute__` on the victim contract:

```cairo
contract_call_helper(
    remaining_gas=remaining_gas,
    block_context=block_context,
    execution_context=execution_context,
);
``` [4](#0-3) 

Two OS-level protections are absent here:

**1. `__validate__` is never called.** `run_validate` is only invoked for top-level transactions, not for syscall-level calls. Since `execute_meta_tx_v0` goes through `contract_call_helper`, the account's `__validate__` entry point is never executed. [5](#0-4) 

**2. Nonce is not checked.** `check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
// Do not handle nonce for version 0.
if (tx_info.version == 0) {
    return ();
}
``` [6](#0-5) 

The `meta_tx_hash` is computed from attacker-controlled inputs (`contract_address`, `calldata`, `chain_id`):

```cairo
let meta_tx_hash = compute_meta_tx_v0_hash(
    contract_address=contract_address,
    entry_point_selector=selector,
    calldata=calldata_start,
    calldata_size=calldata_size,
    chain_id=old_tx_info.chain_id,
);
``` [7](#0-6) 

The hash is passed to the victim's `__execute__` via `TxInfo`, but the OS never verifies the signature against it — that responsibility is left entirely to the victim contract's `__execute__` implementation. Standard account contracts (OpenZeppelin, Argent, Braavos) perform signature verification only in `__validate__`, not in `__execute__`. Therefore, calling `__execute__` directly bypasses all cryptographic authorization.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker can invoke `__execute__` on any victim account contract with arbitrary calldata (e.g., ERC20 `transfer` calls). Since `__validate__` is never run and the OS does not verify the signature, the victim's account will execute the attacker-specified calls — draining tokens, NFTs, or any other assets held by the account — without the account owner's consent or knowledge.

---

### Likelihood Explanation

**High.** The attack requires only:
1. Deploying a malicious contract (permissionless on StarkNet).
2. Sending a single invoke transaction that calls `execute_meta_tx_v0` targeting any victim.

No privileged access, leaked keys, or social engineering is required. The syscall is available to all contracts with no caller restriction. Any account that has not overridden `__execute__` to perform its own signature check (i.e., virtually all standard accounts) is vulnerable.

---

### Recommendation

Add an authorization check inside `execute_meta_tx_v0` to ensure the caller is only permitted to invoke `__execute__` on itself (i.e., `contract_address` must equal `caller_execution_context.execution_info.contract_address`):

```cairo
// Only allow a contract to issue a meta-tx on its own behalf.
assert contract_address = caller_execution_info.contract_address;
```

Alternatively, remove the `execute_meta_tx_v0` syscall entirely if its intended use case (account abstraction bootstrapping) can be served by a safer mechanism that enforces caller identity.

---

### Proof of Concept

1. Attacker deploys malicious contract `M` with the following logic in its `__execute__`:
   - Issue `execute_meta_tx_v0` syscall with:
     - `contract_address = VICTIM_ACCOUNT`
     - `selector = EXECUTE_ENTRY_POINT_SELECTOR`
     - `calldata = [1, ERC20_ADDRESS, TRANSFER_SELECTOR, 3, ATTACKER_ADDRESS, AMOUNT_LOW, 0]`
     - `signature = []` (empty)

2. Attacker sends an invoke transaction calling `M.__execute__`.

3. The OS dispatches `execute_meta_tx_v0`:
   - Computes `meta_tx_hash` from attacker-controlled inputs.
   - Constructs `TxInfo(version=0, nonce=0, account_contract_address=VICTIM_ACCOUNT, signature=[])`.
   - Calls `VICTIM_ACCOUNT.__execute__([1, ERC20_ADDRESS, TRANSFER_SELECTOR, 3, ATTACKER_ADDRESS, AMOUNT_LOW, 0])`.

4. `VICTIM_ACCOUNT.__execute__` (standard account, e.g., OZ) executes the inner call list without verifying the signature (that is `__validate__`'s job, which was never called).

5. The ERC20 `transfer` executes, sending `AMOUNT_LOW` tokens from `VICTIM_ACCOUNT` to `ATTACKER_ADDRESS`.

6. Funds are permanently lost to the victim.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L312-312)
```text
    local contract_address = request.contract_address;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L332-339)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L389-393)
```text
    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L64-67)
```text
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
