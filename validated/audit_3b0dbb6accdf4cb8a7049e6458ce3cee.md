### Title
`compute_meta_tx_v0_hash` Omits Caller Address, Enabling Front-Running and Replay of `meta_tx_v0` Syscall — (File: `transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function does not bind the hash to the contract that invokes the `meta_tx_v0` syscall. Because the caller address is absent from the hash, any contract can present the same user-signed signature and execute the same meta-transaction, enabling front-running and replay attacks that cause direct loss of funds.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` allows any executing contract to call another contract's `__execute__` entry point by supplying a signature and calldata. The OS computes the transaction hash that the target contract will validate against: [1](#0-0) 

```cairo
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
        additional_data_size=0,       // ← no nonce
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
```

The hash covers only: `INVOKE_HASH_PREFIX | version=0 | contract_address | selector | calldata | max_fee=0 | chain_id`.

Two critical fields are absent:

| Missing field | Consequence |
|---|---|
| Caller contract address | Any contract can reuse the same signature |
| Nonce | The same signature can be replayed indefinitely |

The syscall dispatcher makes `meta_tx_v0` reachable by any contract: [2](#0-1) 

Inside `execute_meta_tx_v0`, the caller's identity is never incorporated into the hash: [3](#0-2) 

The resulting `new_tx_info` always carries `nonce=0` and `caller_address=ORIGIN_ADDRESS`: [4](#0-3) 

**Analogy to the external report:**

| External report | This codebase |
|---|---|
| Open delegation: `delegate = ANY_DELEGATE` → same `delegationHash` for all redeemers | `meta_tx_v0`: caller omitted → same `meta_tx_hash` for all callers |
| Anyone can front-run the payment delegation `D2` | Anyone can front-run the `meta_tx_v0` call with the same signature |
| Fix: bind hash to the current redeemer | Fix: bind hash to the caller contract address |

---

### Impact Explanation

**Direct loss of funds (Critical).**

Attack scenario — double execution:

1. Alice signs a meta-tx-v0 message: transfer 100 STRK to Bob.  
   `H = hash(INVOKE_PREFIX, 0, Alice_acct, __execute__, calldata, 0, chain_id)`  
   `sig = Sign(Alice_sk, H)`

2. Relayer R submits a transaction calling `meta_tx_v0(Alice_acct, __execute__, calldata, sig)`.

3. Attacker observes the mempool, extracts `(Alice_acct, calldata, sig)`.

4. Attacker's contract A calls `meta_tx_v0(Alice_acct, __execute__, calldata, sig)` in a front-running transaction. The OS computes the **identical** `meta_tx_hash` (caller not included). Alice's `__execute__` validates the signature — it is valid — and transfers 100 STRK to Bob.

5. R's transaction also executes. If Alice's account has no per-call nonce guard, another 100 STRK is transferred. Alice loses 200 STRK instead of 100 STRK.

Even with per-call nonce guards in the account contract, the OS provides zero replay protection for `meta_tx_v0` (nonce is hardcoded to 0 at the OS level), so accounts that rely on the OS-level nonce mechanism are fully unprotected.

---

### Likelihood Explanation

- **Unprivileged entry path**: any deployed contract can issue the `META_TX_V0_SELECTOR` syscall; no special role is required.
- **Mempool visibility**: StarkNet transactions are observable before inclusion; extracting the signature and calldata is trivial.
- **No cryptographic barrier**: the attacker reuses an already-valid signature; no key material needs to be compromised.
- **Likelihood: High** — the attack requires only mempool monitoring and a deployed contract.

---

### Recommendation

Include the caller contract address (and optionally a nonce) in `compute_meta_tx_v0_hash`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    caller_address: felt,          // ← add caller binding
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
    nonce: felt,                   // ← add nonce for replay protection
) -> felt { ... }
```

Pass `caller_execution_context.execution_info.contract_address` as `caller_address` inside `execute_meta_tx_v0`. This mirrors the fix applied in the referenced delegation framework PR: binding the hash to the specific entity authorised to consume it.

---

### Proof of Concept

```
Block N (mempool):
  Tx T1 (Relayer R):
    call_contract(R)
      → meta_tx_v0(contract=Alice, selector=__execute__,
                   calldata=[transfer, Bob, 100], sig=σ)
    meta_tx_hash = H(PREFIX,0,Alice,__execute__,[transfer,Bob,100],0,chain_id)
    Alice.__execute__(sig=σ, hash=meta_tx_hash) → OK → Alice loses 100 STRK

Block N (front-run by attacker):
  Tx T2 (Attacker contract A, higher gas):
    call_contract(A)
      → meta_tx_v0(contract=Alice, selector=__execute__,
                   calldata=[transfer, Bob, 100], sig=σ)   ← same sig extracted from mempool
    meta_tx_hash = H(PREFIX,0,Alice,__execute__,[transfer,Bob,100],0,chain_id)
                 = SAME HASH (caller A not included)
    Alice.__execute__(sig=σ, hash=meta_tx_hash) → OK → Alice loses 100 STRK again

Net result: Alice loses 200 STRK; only 100 STRK was intended.
``` [1](#0-0) [5](#0-4)

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
