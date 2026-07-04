### Title
Bootstrap Mode in `execute_declare_transaction` Provides Insufficient Protection, Allowing Unauthorized Fee-Free Class Declarations with Permanent Nonce Freeze - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a "bootstrap mode" shortcut that, when triggered, bypasses signature validation, nonce checking, and fee charging entirely. Because the nonce is never incremented in this path, any unprivileged user can craft an unlimited series of declare transactions — each with a different class hash — that are accepted by the OS proof without any authorization or fee payment. This is a direct analog to the basket-mode vulnerability: a special degraded mode activates automatically on attacker-controlled inputs and provides insufficient protection.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the OS checks three conditions before running the normal validation flow:

```cairo
// transaction_impls.cairo lines 761-776
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
``` [1](#0-0) 

When all four conditions are satisfied, the function returns immediately, skipping:

1. **`check_and_increment_nonce`** (line 779) — the nonce at the `'BOOTSTRAP'` address is never read from state and never incremented.
2. **`non_reverting_select_execute_entry_point_func`** with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR` (lines 803–806) — the account's `__validate_declare__` entry point is never called; no signature is verified.
3. **`charge_fee`** (lines 822–824) — no fee is transferred to the sequencer. [2](#0-1) 

The four trigger conditions are entirely attacker-controlled:

| Condition | How attacker satisfies it |
|---|---|
| `sender_address == 'BOOTSTRAP'` | Set `sender_address` to the felt literal `0x424F4F545354524150` in the transaction |
| `tx_info.nonce == 0` | Set nonce field to 0 in the transaction |
| `tx_info.version == 3` | Use transaction version 3 |
| `max_possible_fee == 0` | Set all three resource bounds (`l1_gas`, `l2_gas`, `l1_data_gas`) max amounts to 0 |

`'BOOTSTRAP'` is a plain Cairo felt literal — it is not a privileged role, a key, or an access-controlled address. Any user can craft a declare transaction with `sender_address` equal to that felt value. The transaction hash is computed from these fields and verified by `AssertTransactionHash`, so the prover must supply a valid transaction; but since signature validation is entirely skipped in this path, no private key for the `'BOOTSTRAP'` address is required.

**The nonce-freeze root cause:** Because `check_and_increment_nonce` is skipped, the nonce stored in `contract_state_changes` for the `'BOOTSTRAP'` address is never incremented. The condition `tx_info.nonce == 0` therefore remains satisfiable for every subsequent transaction. An attacker can submit transaction 1 (nonce=0, class A), transaction 2 (nonce=0, class B), transaction 3 (nonce=0, class C), … indefinitely. The `prev_value=0` guard in `dict_update` only prevents re-declaring the same class hash; it does not prevent declaring arbitrarily many distinct class hashes. [3](#0-2) 

The dispatch path that reaches this function is unconditional — `execute_transactions_inner` routes any `DECLARE` transaction type directly to `execute_declare_transaction` with no prior guard: [4](#0-3) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

Because fees are completely bypassed and the nonce is never incremented, an attacker can submit an unbounded stream of zero-cost declare transactions. Each transaction is a valid state-changing operation that the OS accepts and includes in the proven state update. Block space is finite; flooding it with free, provably-valid declare transactions crowds out all legitimate transactions. Sequencers that enforce fee-based ordering cannot reject these transactions at the OS level because the proof is sound. The result is effective denial of service against the entire transaction pipeline — a total network shutdown matching the High impact category.

---

### Likelihood Explanation

**High.** The attack requires no special access, no leaked key, no privileged role, and no external dependency. Any user who can submit a declare transaction to the network can trigger bootstrap mode by setting four fields in the transaction to specific public values. The conditions are stable across blocks because the nonce is never incremented. The attack is immediately repeatable.

---

### Recommendation

1. **Remove or gate the bootstrap path behind a block-number or config-hash check.** Bootstrap mode should only be active during a provably bounded initialization window (e.g., `block_number == 0`), enforced in Cairo, not just by sequencer policy.
2. **Increment the nonce even in bootstrap mode.** If the bootstrap path must exist, `check_and_increment_nonce` must still be called so that each bootstrap declare can only be used once per nonce value.
3. **Require a non-zero fee or a privileged signature for bootstrap declares.** The current design allows zero-cost, zero-authorization class registration, which is exploitable at any time the conditions are met.

---

### Proof of Concept

1. Attacker constructs a declare transaction with:
   - `sender_address = 0x424F4F545354524150` (felt encoding of `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `l1_gas_max_amount = 0`, `l2_gas_max_amount = 0`, `l1_data_gas_max_amount = 0`
   - `class_hash` = hash of any valid Sierra class the attacker controls
   - `compiled_class_hash` = corresponding CASM hash

2. Attacker submits this transaction. The sequencer includes it in a block.

3. The OS executes `execute_declare_transaction`. The transaction hash is computed and verified. The bootstrap condition at line 764 is satisfied. The OS calls `dict_update` to register the class and returns — skipping `check_and_increment_nonce`, `__validate_declare__`, and `charge_fee`.

4. The `'BOOTSTRAP'` address nonce in `contract_state_changes` remains 0.

5. Attacker repeats step 1–4 with a different `class_hash`. The nonce condition `tx_info.nonce == 0` is still satisfied. A second class is declared for free.

6. Attacker repeats indefinitely, filling blocks with zero-cost class declarations and starving legitimate transactions of block space. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L63-88)
```text
from starkware.starknet.core.os.state.commitment import StateEntry
from starkware.starknet.core.os.transaction_hash.transaction_hash import (
    CommonTxFields,
    compute_declare_transaction_hash,
    compute_deploy_account_transaction_hash,
    compute_invoke_transaction_hash,
    compute_l1_handler_transaction_hash,
    update_pedersen_in_builtin_ptrs,
    update_poseidon_in_builtin_ptrs,
)

// Returns the transaction's initial gas derived from its resource bounds.
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}

// Represents the calldata of an ERC20 transfer.
struct TransferCallData {
    recipient: felt,
    amount: Uint256,
}

// Returns the maximum possible fee that can be charged for the transaction.
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-825)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions_inner.cairo (L58-62)
```text
    assert tx_type = 'DECLARE';
    // Handle the declare transaction.
    execute_declare_transaction(block_context=block_context);
    %{ ExitTx %}
    return execute_transactions_inner(block_context=block_context, n_txs=n_txs - 1);
```
