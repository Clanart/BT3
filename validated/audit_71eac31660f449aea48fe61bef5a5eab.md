### Title
Unguarded Bootstrap Path in `execute_declare_transaction` Allows Fee-Free, Signature-Free Class Declaration — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special-case "bootstrap" branch that, when triggered with fully user-controllable transaction fields (`sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, all resource bounds == 0), silently skips signature verification (`run_validate`), fee charging (`charge_fee`), and nonce increment (`check_and_increment_nonce`), and directly writes a class hash into the state. Because every triggering condition is a plain felt field in the transaction that any sender can set, this is an unprotected state-transition bypass analogous to the public `withdrawToken()` that could be called with amount = 0 to detach a token without withdrawing.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the following branch is evaluated:

```cairo
if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
    let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
    if (max_possible_fee == 0) {
        assert_not_zero(compiled_class_hash);
        dict_update{dict_ptr=contract_class_changes}(
            key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
        );
        %{ SkipTx %}
        return ();
    }
}
``` [1](#0-0) 

When this branch is taken, the function returns immediately, skipping:

1. **`check_and_increment_nonce`** — the 'BOOTSTRAP' address nonce is never incremented, so the same nonce=0 condition remains valid for every subsequent bootstrap declare.
2. **`run_validate`** — no `__validate_declare__` entry point is called; no signature is checked.
3. **`charge_fee`** — no ERC-20 transfer is executed; the sequencer receives nothing. [2](#0-1) 

All four triggering conditions are plain felt fields in the transaction body:

| Condition | Who controls it |
|---|---|
| `sender_address == 'BOOTSTRAP'` | Transaction sender (any user) |
| `tx_info.nonce == 0` | Transaction sender |
| `tx_info.version == 3` | Transaction sender |
| `max_possible_fee == 0` (all resource bounds = 0) | Transaction sender |

`compute_max_possible_fee` returns 0 when all `max_amount` fields in the three resource-bound slots are 0, which is a valid encoding accepted by the OS. [3](#0-2) 

The class-hash pre-image check (`finalize_class_hash`) and the compiled-class-hash non-zero assertion still run, so the declared class must be a structurally valid Sierra/CASM class. However, no account authorization is required. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).** `charge_fee` performs an ERC-20 `transfer` from the account to the sequencer address. Bypassing it means the sequencer receives zero tokens for processing the transaction. Because the nonce is never incremented, an attacker can submit an unbounded stream of bootstrap-declare transactions (each with a distinct valid class hash), each of which is provably valid at the OS level and each of which pays zero fees. The sequencer's fee revenue — which is the economic mechanism securing liveness — is directly drained to zero for every such transaction included in a block. [5](#0-4) 

**Invalid class acceptance (High).** Classes are registered into the global state without any account signature. The `dict_update` with `prev_value=0` writes the `compiled_class_hash` into `contract_class_changes`, which is then committed to the Patricia tree in `state_update`. A class declared this way is indistinguishable from a legitimately declared class in all subsequent OS executions. [6](#0-5) 

---

### Likelihood Explanation

Every triggering condition is a user-supplied felt field. No private key, no privileged role, and no special on-chain state is required. The felt literal `'BOOTSTRAP'` encodes to a fixed value (0x424f4f545354524150); any user can place that value in the `sender_address` field of a V3 declare transaction. Because the nonce is never incremented, the same attacker can repeat the attack indefinitely across multiple blocks. The only practical gate is whether the sequencer's gateway layer rejects such transactions before they reach the OS, but the OS itself — which is the proven artifact — imposes no such restriction.

---

### Recommendation

1. **Remove the bootstrap path entirely** from the production OS. Bootstrapping should be handled off-chain or through a separate, cryptographically authenticated mechanism (e.g., a privileged sequencer key whose public key is committed in the OS config).
2. If the bootstrap path must remain, gate it on a verifiable condition that cannot be forged by an unprivileged user — for example, require a valid signature from a key whose hash is embedded in `StarknetOsConfig`, or restrict it to block number 0 only.
3. At minimum, always call `check_and_increment_nonce` so that each bootstrap slot can be used at most once per nonce value.

---

### Proof of Concept

1. Construct a valid Sierra contract class `C` and its CASM compiled class hash `H`.
2. Submit a V3 declare transaction with:
   - `sender_address = 0x424f4f545354524150` (`'BOOTSTRAP'` as a felt)
   - `nonce = 0`
   - `version = 3`
   - `l1_gas.max_amount = 0`, `l2_gas.max_amount = 0`, `l1_data_gas.max_amount = 0`
   - `class_hash = hash(C)`, `compiled_class_hash = H`
   - `signature = []` (empty — `run_validate` is never called)
3. The OS evaluates the bootstrap branch, calls `dict_update(key=hash(C), prev_value=0, new_value=H)`, and returns.
4. `state_update` commits the new class to the Patricia tree. The class is now globally declared.
5. No fee was transferred. Repeat with a different class hash; the 'BOOTSTRAP' nonce remains 0. [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-102)
```text
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
    let n_resource_bounds = (tx_info.resource_bounds_end - resource_bounds) / ResourceBounds.SIZE;

    // Only V3 transactions with all resource bounds are supported.
    assert tx_info.version = 3;
    assert n_resource_bounds = 3;

    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L735-744)
```text
        local contract_class_component_hashes: ContractClassComponentHashes*;
        %{ SetComponentHashes %}

        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L764-776)
```text
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-827)
```text
    // Increment nonce.
    check_and_increment_nonce(tx_info=tx_info);

    // Prepare the validate execution context.
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(key=sender_address);
    // The calldata for declare tx is the class hash.
    local validate_declare_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=1,
        calldata=class_hash_ptr,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_info,
            caller_address=ORIGIN_ADDRESS,
            contract_address=sender_address,
            selector=VALIDATE_DECLARE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=deprecated_tx_info,
    );

    let remaining_gas = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        // Run the account contract's "__validate_declare__" entry point.
        %{ StartTx %}
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
    // TODO(Yoni): calculate the gas consumed and use it to charge fee (for all transactions).
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );

    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
    %{ EndTx %}

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L76-88)
```text
    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );

```
