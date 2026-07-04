### Title
Missing Nonce in `meta_tx_v0` Transaction Hash Enables Unbounded Replay of Signed Operations — (File: `execution/syscall_impls.cairo`)

---

### Summary

`execute_meta_tx_v0` computes a transaction hash that omits a nonce and does not trigger any OS-level nonce increment. Any contract can invoke this syscall targeting any account's `__execute__` entry point. Because the hash is deterministic over `(contract_address, selector, calldata, chain_id)` alone, a valid meta-transaction observed on-chain can be replayed an unlimited number of times, enabling direct loss of funds from the victim account.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` (lines 286–400) is a syscall reachable by any executing contract. It constructs a new `TxInfo` and calls the target contract's `__execute__` entry point directly, bypassing `__validate__` entirely.

**Root cause 1 — No access control on the caller.**
The function imposes no restriction on which contract may invoke it: [1](#0-0) 

Any contract executing inside the OS can call `meta_tx_v0` targeting any account address.

**Root cause 2 — Nonce absent from the transaction hash.**
The hash is computed as: [2](#0-1) 

Parameters passed are `contract_address`, `entry_point_selector`, `calldata`, `calldata_size`, and `chain_id`. No nonce is included. The resulting hash is therefore identical for every replay of the same logical operation.

**Root cause 3 — OS nonce is never incremented for meta_tx_v0.**
For regular account transactions the OS calls `check_and_increment_nonce`, which updates `StateEntry.nonce`. `execute_meta_tx_v0` never calls this function. The `TxInfo` it constructs hard-codes `nonce=0`: [3](#0-2) 

**Root cause 4 — `__validate__` is skipped.**
The execution context is built with `entry_point_type=ENTRY_POINT_TYPE_EXTERNAL` and `selector=EXECUTE_ENTRY_POINT_SELECTOR`, jumping directly to `__execute__`: [4](#0-3) 

Standard account contracts perform signature validation in `__validate__`, not in `__execute__`. Because `__validate__` is never invoked, the account's signature check is bypassed entirely for any account whose `__execute__` does not independently re-validate the signature.

The syscall is dispatched without restriction from the main OS syscall loop: [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

Two concrete attack paths exist:

**Path A (replay):** A victim signs a meta_tx_v0 authorising a token transfer. A relayer submits it. An attacker observes the signature on-chain, deploys a contract that calls `meta_tx_v0` with the identical parameters and signature, and replays the operation. Because the hash is nonce-free and the OS nonce is never incremented, the same `(hash, signature)` pair is valid on every subsequent block. The attacker repeats until the victim's balance is zero.

**Path B (signature bypass):** For accounts whose `__execute__` does not independently validate the signature (the common case, since validation lives in `__validate__`), the attacker supplies an arbitrary or empty signature. The OS executes `__execute__` unconditionally, allowing the attacker to drain any account by crafting calldata that transfers funds to themselves.

---

### Likelihood Explanation

**Medium–High.**

- Path A requires only that the attacker observe one legitimate meta_tx_v0 on-chain — a passive, zero-cost operation.
- Path B requires no prior signature at all; the attacker only needs to know the target account address and the ERC-20 transfer calldata.
- The entry point is reachable by any unprivileged transaction sender who deploys a contract that issues the `meta_tx_v0` syscall.
- No trusted role, leaked key, or network-level capability is required.

---

### Recommendation

1. **Include a nonce in `compute_meta_tx_v0_hash`** — add a per-account, per-meta-tx nonce field to the hash inputs so that each signed meta-transaction is bound to a single use.
2. **Increment the OS nonce** — call `check_and_increment_nonce` (or an equivalent) inside `execute_meta_tx_v0` so the OS state tracks meta-tx consumption.
3. **Alternatively, require `__validate__` to run** — restructure meta_tx_v0 so that the account's `__validate__` entry point is invoked before `__execute__`, restoring the standard security model.

---

### Proof of Concept

```
1. Victim V has account at address A with 100 STRK.
2. V signs a meta_tx_v0 authorising: transfer(attacker, 10 STRK).
   Hash = H(A, __execute__, calldata, chain_id)  [no nonce]
3. Relayer submits the meta_tx_v0; 10 STRK transferred. OS nonce for A: unchanged (still 0).
4. Attacker deploys MaliciousContract with:
       fn attack() { meta_tx_v0(A, __execute__, calldata, sig); }
5. Attacker submits invoke → MaliciousContract.attack().
   OS calls execute_meta_tx_v0 → same hash H → same sig valid → __execute__ runs → 10 STRK transferred.
6. Attacker repeats step 5 nine more times → A drained of 100 STRK.
   Each iteration: hash identical, OS nonce never incremented, no replay guard.
``` [6](#0-5) [3](#0-2) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L286-310)
```text
func execute_meta_tx_v0{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    alloc_locals;

    let request = cast(syscall_ptr + RequestHeader.SIZE, MetaTxV0Request*);
    local calldata_start: felt* = request.calldata_start;
    local calldata_size = request.calldata_end - calldata_start;

    let specific_base_gas_cost = (
        META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size
    );
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=MetaTxV0Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-340)
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
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L366-393)
```text
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=calldata_size,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=new_tx_info,
            caller_address=ORIGIN_ADDRESS,
            contract_address=contract_address,
            selector=selector,
        ),
        deprecated_tx_info=deprecated_tx_info_ptr,
    );
    fill_deprecated_tx_info(tx_info=new_tx_info, dst=execution_context.deprecated_tx_info);

    // Since we process the revert log backwards, entries before this point belong to the calling
    // contract.
    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY, value=caller_execution_info.contract_address
    );
    let revert_log = &revert_log[1];

    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );
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
