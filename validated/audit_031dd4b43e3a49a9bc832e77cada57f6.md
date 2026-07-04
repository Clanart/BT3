### Title
Unrestricted BOOTSTRAP Declare Path Bypasses Signature Verification, Nonce Enforcement, and Fee Charging — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special-cased early-return branch gated only on felt-literal equality (`sender_address == 'BOOTSTRAP'`), a zero nonce, version 3, and zero resource bounds. This branch skips `run_validate` (signature verification), `check_and_increment_nonce` (replay protection), and `charge_fee` (fee accounting). Because the nonce is never incremented in this path, the condition `nonce == 0` is permanently satisfiable for the BOOTSTRAP address, making the bypass repeatable across an unbounded number of declare transactions.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the OS checks:

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

When this branch is taken, the function returns before reaching:

- `check_and_increment_nonce` at line 779 — nonce is never written back to `contract_state_changes`, so the BOOTSTRAP address's on-chain nonce stays at 0 permanently.
- `run_validate` — the account's `__validate_declare__` entry point is never called; no ECDSA/Stark signature is checked.
- `charge_fee` — no ERC-20 transfer is executed; the sequencer receives nothing. [2](#0-1) 

The only structural guard against re-use is `prev_value=0` in `dict_update`, which prevents re-declaring the same class hash. It does not prevent declaring an unlimited number of *distinct* valid Sierra class hashes for free.

The `'BOOTSTRAP'` literal is a Cairo short-string felt — a fixed, publicly known numeric value. Any party who can influence block contents (i.e., the sequencer) can craft a V3 declare transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, and all resource bounds set to zero, and the OS will accept it as protocol-valid without any account authorization.

---

### Impact Explanation

**Direct loss of funds (Critical):** The fee token ERC-20 transfer that compensates the sequencer/fee recipient is entirely skipped. An attacker exploiting this path can declare an arbitrary number of valid Sierra classes at zero cost. Each such transaction represents a fee that should have been collected but was not — a direct, provable loss of funds from the fee recipient's perspective, enforced (or rather, not enforced) at the OS/proof layer.

**Invalid transaction acceptance:** The OS generates a proof attesting that these fee-free, signature-free declare transactions are valid protocol state transitions. The L1 verifier accepts this proof. The resulting on-chain state (new class hashes registered) is indistinguishable from legitimately declared classes, meaning downstream contracts can be deployed from these classes with full protocol legitimacy.

---

### Likelihood Explanation

The attack requires the ability to include a crafted declare transaction in a block — currently gated by the sequencer. However, the OS is the cryptographic enforcement layer; the sequencer's off-chain filtering is not a protocol guarantee. A sequencer that is compromised, incentivized, or simply running a modified client can include BOOTSTRAP transactions. Because the OS proof is what the L1 verifier trusts, a valid proof containing BOOTSTRAP declares is indistinguishable from a legitimate one. The nonce-never-increments property means the window of exploitation is permanent, not one-time.

---

### Recommendation

1. Remove the BOOTSTRAP branch entirely from the production OS. Bootstrapping should be handled off-chain or through a separate, audited mechanism that does not bypass the core security invariants of the OS.
2. If the branch must remain, gate it with an explicit `assert False` or equivalent so it is unreachable in production builds, and enforce it only in a separate test-only compilation target.
3. At minimum, `check_and_increment_nonce` must be called even in the BOOTSTRAP path to prevent indefinite replay.

---

### Proof of Concept

1. Craft a V3 declare transaction:
   - `sender_address = <felt value of 'BOOTSTRAP'>`
   - `nonce = 0`
   - `version = 3`
   - All resource bounds: `max_amount = 0`, `max_price_per_unit = 0`
   - `compiled_class_hash` = any valid, previously-undeclared Sierra compiled class hash
   - Signature: arbitrary (never checked)

2. Submit to a sequencer (or operate a sequencer directly).

3. The OS processes the transaction via `execute_declare_transaction`. The BOOTSTRAP branch fires, `dict_update` registers the class hash with `prev_value=0`, and the function returns. No fee is charged, no signature is verified, no nonce is incremented.

4. Repeat step 1–3 with a different `compiled_class_hash`. The BOOTSTRAP address nonce is still 0 in state; the condition `tx_info.nonce == 0` is satisfied again. The OS accepts the second transaction identically.

5. The resulting proof is valid and accepted by the L1 verifier. All declared classes are now live on-chain with zero fee paid. [1](#0-0) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L104-165)
```text
// Charges a fee from the user.
// If max_fee is not 0, validates that the selector matches the entry point of an account contract
// and executes an ERC20 transfer on the behalf of that account contract.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the transaction that pays the fee.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-776)
```text
    // Do not run validate or perform any account-related actions for declare transactions that
    // meet the following conditions.
    // This flow is used for the sequencer to bootstrap a new system.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-828)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-88)
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
```
