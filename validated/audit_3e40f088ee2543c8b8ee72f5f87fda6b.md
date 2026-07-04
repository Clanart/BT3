### Title
Signature Verification Bypass via `execute_meta_tx_v0` Syscall Allows Unauthorized Execution of Any Account's `__execute__` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall constructs a version-0 transaction context and directly invokes the target account contract's `__execute__` entry point via `contract_call_helper`, without ever calling the target's `__validate__` entry point. Because `run_validate` unconditionally skips validation for `version == 0` transactions, and because `execute_meta_tx_v0` never calls `run_validate` at all, the target account's signature verification is completely bypassed. Any unprivileged transaction sender can exploit this to execute arbitrary calls on behalf of any account contract, leading to direct loss of funds.

---

### Finding Description

**Root cause — `execute_meta_tx_v0` in `syscall_impls.cairo`:**

The function creates a new `TxInfo` with `version=0` and `nonce=0`, then calls `contract_call_helper` directly with the target's `__execute__` selector: [1](#0-0) 

The `version=0` and `nonce=0` are hardcoded into the synthesized `TxInfo`. The attacker-supplied `request.signature_start/end` is placed into the new `TxInfo` without any verification: [2](#0-1) 

`contract_call_helper` is then called directly — `run_validate` is never invoked for the target contract: [3](#0-2) 

**Compounding design — `run_validate` in `execute_transaction_utils.cairo`:**

Even if `run_validate` were called, it unconditionally returns early for `version == 0`: [4](#0-3) 

This means the OS-level guarantee that `__validate__` runs before `__execute__` is broken for any contract targeted by `meta_tx_v0`.

**Nonce protection also absent:**

`check_and_increment_nonce` also skips nonce enforcement for `version == 0`: [5](#0-4) 

`execute_meta_tx_v0` does not call `check_and_increment_nonce` at all, so there is no replay protection either.

**Analog to ECDSA signature malleability:**

The external report describes a flaw where `ECDSA.recover()` accepts alternative signature encodings, allowing a different byte sequence to pass as a valid signature for the same message — effectively bypassing the authentication check. The analog here is structurally identical: `execute_meta_tx_v0` accepts an attacker-supplied signature array and passes it into the target's `TxInfo`, but the OS never calls the target's `__validate__` to verify it. The authentication step is skipped entirely, not merely weakened.

---

### Impact Explanation

In StarkNet's account abstraction model, `__validate__` is the sole OS-enforced signature verification gate before `__execute__` runs. If `__validate__` is bypassed, an attacker can supply arbitrary calldata to any account's `__execute__` — for example, a `transfer` call to drain ERC-20 tokens to the attacker's address. The target account's `__execute__` receives `tx_info` with `version=0` and the attacker's chosen calldata; since `__validate__` never ran, no signature was checked.

**Impact: Critical — Direct loss of funds.**

---

### Likelihood Explanation

The attack path requires only:
1. A valid StarkNet account (to send the outer invoke transaction).
2. A deployed contract (or the attacker's own account `__execute__`) that calls the `meta_tx_v0` syscall with the victim's address and attacker-chosen calldata.

No privileged role, leaked key, or operator cooperation is needed. The syscall is available to any executing contract. The attacker controls `request.contract_address`, `request.calldata_start/end`, and `request.signature_start/end` entirely.

**Likelihood: High.**

---

### Recommendation

1. **Call `run_validate` for the target contract before `contract_call_helper`** inside `execute_meta_tx_v0`, using the target's class hash and the provided signature, so the target's `__validate__` entry point is always executed.
2. **Remove the unconditional `version == 0` skip** in `run_validate`, or ensure that `meta_tx_v0` never produces a context where `__validate__` is skipped.
3. **Enforce nonce tracking** for meta transactions to prevent replay.
4. **Restrict `meta_tx_v0` availability** to only contracts that have been explicitly authorized, if the feature is intended for a narrow use case.

---

### Proof of Concept

```
1. Attacker deploys MaliciousContract with a function `attack(victim_address, token_address)`.

2. Inside `attack`, MaliciousContract calls the `meta_tx_v0` syscall with:
   - contract_address = victim_address
   - selector        = EXECUTE_ENTRY_POINT_SELECTOR  (__execute__)
   - calldata        = [transfer(attacker_address, victim_balance)]
   - signature       = []  (empty — never verified)

3. OS executes execute_meta_tx_v0:
   - Constructs TxInfo { version=0, nonce=0, signature=[] }
   - Calls contract_call_helper → victim.__execute__(calldata)
   - run_validate is NEVER called → victim.__validate__ NEVER runs

4. victim.__execute__ processes the transfer call without any signature check.
   Victim's tokens are transferred to the attacker.

5. The outer transaction (attacker's invoke) is valid and committed.
   The block is finalized with the theft included.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L286-390)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L116-158)
```text
func run_validate{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;
    local tx_execution_info: ExecutionInfo* = tx_execution_context.execution_info;

    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }

    // "__validate__" is expected to get the same calldata as "__execute__".
    local validate_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=tx_execution_context.class_hash,
        calldata_size=tx_execution_context.calldata_size,
        calldata=tx_execution_context.calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_execution_info.tx_info,
            caller_address=tx_execution_info.caller_address,
            contract_address=tx_execution_info.contract_address,
            selector=VALIDATE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    // The __validate__ function should not revert.
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    return ();
```
