### Title
Missing Nonce Validation and Increment in `execute_meta_tx_v0` Enables Unbounded Meta-Transaction Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall handler in the StarkNet OS does not check or increment the target contract's nonce before executing its `__execute__` entry point. Additionally, `compute_meta_tx_v0_hash` omits the nonce from the hash preimage. This is directly analogous to the external report's pattern: a state-transition function that moves execution power (here, executing a target account's `__execute__`) without checking or updating the "already-used" state (the nonce). An attacker who possesses a victim's signed meta-transaction can replay it an arbitrary number of times within a single block, causing direct loss of funds.

---

### Finding Description

**Vulnerability class**: State-transition bypass / fee-accounting bug — missing "already-used" state check before allowing repeated execution.

**Root cause — `execute_meta_tx_v0` (lines 286–400, `syscall_impls.cairo`)**

The function constructs a synthetic `TxInfo` for the target contract with a hardcoded `nonce=0` and `version=0`: [1](#0-0) 

It then calls `contract_call_helper` directly, which executes the target contract's `__execute__` entry point: [2](#0-1) 

At no point is `check_and_increment_nonce` called for the target contract. Compare this with every other transaction type in `transaction_impls.cairo`, which all call `check_and_increment_nonce` before or after execution:

- `execute_invoke_function_transaction` — calls it at line 311 [3](#0-2) 
- `execute_deploy_account_transaction` — calls it at line 651 [4](#0-3) 
- `execute_declare_transaction` — calls it at line 779 [5](#0-4) 

**Root cause — `compute_meta_tx_v0_hash` omits nonce from preimage**

The hash is computed from only `contract_address`, `entry_point_selector`, `calldata`, and `chain_id`: [6](#0-5) 

Because no nonce is included in the hash, the same signature is cryptographically valid for every replay of the same calldata.

**Root cause — `check_and_increment_nonce` skips version-0**

Even if `check_and_increment_nonce` were called with the synthetic `TxInfo` (version=0), it would be a no-op: [7](#0-6) 

So the target contract's nonce in `contract_state_changes` is never updated, meaning any in-`__execute__` nonce check against on-chain state also passes every time.

**Root cause — `__validate__` is never called**

`execute_meta_tx_v0` enforces `selector == EXECUTE_ENTRY_POINT_SELECTOR` and calls `__execute__` directly, bypassing `__validate__` entirely: [8](#0-7) 

Standard account contracts perform nonce and signature validation in `__validate__`. Skipping it removes the primary replay-protection gate.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker who obtains a victim's signed meta-transaction (e.g., by acting as a relayer, or by intercepting a broadcast) can:

1. Deploy a malicious contract `M` whose `__execute__` calls `META_TX_V0` N times with the victim's signature and calldata.
2. Submit one invoke transaction calling `M`.
3. Each of the N `META_TX_V0` calls executes the victim's `__execute__` with `nonce=0` and the same valid signature.
4. Because the OS never increments the victim's nonce and the hash never changes, every call succeeds.
5. If the victim's `__execute__` transfers funds (e.g., ERC-20 transfer), the victim loses N × amount instead of 1 × amount.

The attacker is an unprivileged transaction sender. No privileged role or key is required beyond possession of the victim's already-broadcast signed meta-transaction.

---

### Likelihood Explanation

**Medium-High.** The `META_TX_V0` syscall is available to any deployed contract. The attack is viable whenever:

- A user signs a meta-transaction for a relayer (a common pattern for gasless transactions).
- The relayer (or any party who intercepts the signed payload) is malicious or compromised.

The signed payload is sufficient to replay indefinitely; no additional secrets are needed. The attack is executable within a single block by any unprivileged sender.

---

### Recommendation

1. **Include a nonce in `compute_meta_tx_v0_hash`**: Add the target contract's current on-chain nonce to the hash preimage so that each signed meta-transaction is bound to a single use.

2. **Call `check_and_increment_nonce` for the target contract** inside `execute_meta_tx_v0`, using the target contract's actual on-chain nonce (not the hardcoded `0`), so the OS state reflects consumption of the meta-transaction.

3. **Alternatively, call `__validate__`** on the target contract before `__execute__`, consistent with how all other transaction types are processed, so that the account's own replay-protection logic is enforced.

The fix is analogous to the external report's recommendation: add a check equivalent to `if (s_hasVotedByTokenId[tokenId_]) revert` — here, assert and increment the target nonce before allowing `__execute__` to proceed.

---

### Proof of Concept

```
1. Victim Alice signs a meta-tx: transfer 100 STRK to Bob.
   Signature covers: H(alice_addr, __execute__, calldata, chain_id) — no nonce.

2. Bob deploys MaliciousRelayer with __execute__:
     for i in 0..10:
         syscall META_TX_V0(
             contract_address = alice_addr,
             selector         = __execute__,
             calldata         = [transfer(bob, 100)],
             signature        = alice_signature,   // same every iteration
         )

3. Bob submits invoke(MaliciousRelayer.__execute__).

4. OS processes each META_TX_V0 call:
   - Computes meta_tx_hash = H(alice_addr, __execute__, calldata, chain_id)  [same each time]
   - Sets new_tx_info.nonce = 0, version = 0
   - Calls alice.__execute__ with this TxInfo
   - Does NOT call check_and_increment_nonce → alice's nonce in contract_state_changes stays 0
   - Alice's __execute__ sees nonce=0 matching on-chain nonce=0 → passes
   - Transfer of 100 STRK executes

5. After 10 iterations: Alice has lost 1000 STRK; Bob received 1000 STRK.
   Only 1 outer transaction nonce was consumed (Bob's).
``` [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L286-400)
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

    local contract_address = request.contract_address;
    local selector = request.selector;
    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local old_tx_info: TxInfo* = caller_execution_info.tx_info;

    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    // Sanity check: Verify that `signature` is a valid Sierra array.
    assert_nn_le(request.signature_end - request.signature_start, SIERRA_ARRAY_LEN_BOUND - 1);

    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

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

    // Prepare execution context.
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

    let (deprecated_tx_info_ptr: DeprecatedTxInfo*) = alloc();
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

    // Entries before this point belong to the callee.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-311)
```text
    check_and_increment_nonce(tx_info=tx_info);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L651-651)
```text
    check_and_increment_nonce(tx_info=tx_info);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L779-779)
```text
    check_and_increment_nonce(tx_info=tx_info);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-89)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }

    tempvar state_entry: StateEntry*;
    %{ SetStateEntryToAccountContractAddress %}

    tempvar current_nonce = state_entry.nonce;
    with_attr error_message("Unexpected nonce.") {
        assert current_nonce = tx_info.nonce;
    }

    // Update contract_state_changes.
    tempvar new_state_entry = new StateEntry(
        class_hash=state_entry.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=current_nonce + 1,
    );
    dict_update{dict_ptr=contract_state_changes}(
        key=tx_info.account_contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    return ();
}
```
