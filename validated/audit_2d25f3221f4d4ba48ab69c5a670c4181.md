### Title
Unchecked Return Value of Fee Transfer in `charge_fee` Allows Silent Fee Payment Failure - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS program calls `non_reverting_select_execute_entry_point_func` to execute an ERC20 fee transfer, but **completely discards the return value**. Because the function is explicitly non-reverting (it catches internal failures and surfaces them as return data rather than aborting), a failed fee transfer produces no observable effect on the OS execution path. The OS program will generate a valid proof for a block in which fees were never actually paid, enabling fee-free transaction execution.

---

### Finding Description

In `transaction_impls.cairo`, the `charge_fee` function is responsible for deducting the actual fee from the user's account by calling the ERC20 fee token's `transfer` entry point:

```cairo
// Lines 160-164
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

`non_reverting_select_execute_entry_point_func` returns a tuple `(retdata_size, retdata, is_deprecated)`. When the underlying ERC20 `transfer` call fails (e.g., insufficient balance, contract panic), the function does **not** revert the OS execution — it returns a failure indicator in `retdata`. However, `charge_fee` does not capture or inspect this return value at all.

Contrast this with every other call site of the same function in the same file, where the return value **is** captured and validated:

```cairo
// Line 677 — validate_deploy path
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_deploy_execution_context
);
// ...
assert retdata[0] = VALIDATED;
``` [2](#0-1) 

```cairo
// Line 804 — validate_declare path
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_declare_execution_context
);
// ...
assert retdata[0] = VALIDATED;
``` [3](#0-2) 

The `charge_fee` function is called for every account transaction type — invoke, deploy-account, and declare — making this a systemic gap:

- Invoke: line 361 [4](#0-3) 
- Deploy-account: line 687 [5](#0-4) 
- Declare: line 822 [6](#0-5) 

---

### Impact Explanation

The StarkNet OS Cairo program is the authoritative source of truth for what gets proven and committed to L1. If the OS program does not enforce that the fee ERC20 transfer succeeded, a prover can generate a **valid STARK proof** for a block in which:

1. User transactions were fully executed (state changes applied, L2→L1 messages emitted, etc.).
2. The fee token balance of the user was **never decremented**.
3. The sequencer/fee recipient **never received the fee**.

The L1 verifier contract accepts the proof because the proof is mathematically valid — the OS program itself did not assert fee transfer success. This constitutes a **direct loss of funds**: fees owed to the sequencer (or fee recipient) are provably not collected, and the state commitment accepted on L1 reflects this.

**Allowed impact matched:** Critical — Direct loss of funds.

---

### Likelihood Explanation

The exploit requires a sequencer (or a prover colluding with a sequencer) to construct a block where the fee token transfer fails. In the current centralized sequencer model this requires the sequencer itself to act maliciously. As StarkNet moves toward decentralized sequencing, any node that wins a block-production slot can exploit this. The root cause is in the proven OS program, not in off-chain sequencer software, so no off-chain mitigation can close the gap — only a fix to the Cairo program matters.

---

### Recommendation

Capture and assert the return value of `non_reverting_select_execute_entry_point_func` inside `charge_fee`, consistent with how every other call site in the same file handles it:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// Assert the transfer succeeded.
if (is_deprecated == 0) {
    // ERC20 transfer returns (true,) on success.
    assert retdata_size = 1;
    assert retdata[0] = 1;  // TRUE
}
```

This mirrors the pattern already used for `__validate__`, `__validate_deploy__`, and `__validate_declare__` entry points in the same file.

---

### Proof of Concept

1. A sequencer constructs a block containing an invoke transaction from account `A`, which has zero fee-token balance.
2. The OS executes the transaction body normally (state changes committed).
3. `charge_fee` is called. It builds an ERC20 `transfer` calldata for `actual_fee` tokens from `A` to the sequencer.
4. `non_reverting_select_execute_entry_point_func` executes the transfer. The ERC20 contract panics (insufficient balance). The function catches the panic and returns `retdata` indicating failure — but does **not** revert the OS trace.
5. `charge_fee` ignores the return value and returns normally.
6. The OS program completes without any assertion failure. A valid STARK proof is generated.
7. The L1 verifier accepts the proof. The state transition is finalized on L1 with `A`'s fee-token balance unchanged and the sequencer having received zero fees.
8. The sequencer has effectively allowed `A` to execute a transaction for free, resulting in a direct loss of the fee that should have been collected. [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-165)
```text
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }

    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);

    // TODO(ilya, 01/01/2026): Consider caching the fee_token_class_hash.
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-683)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
    }
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L686-688)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=validate_deploy_execution_context);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L804-811)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
    // TODO(Yoni): calculate the gas consumed and use it to charge fee (for all transactions).
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
