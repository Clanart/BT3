### Title
No Minimum `max_price_per_unit` Enforcement Allows Complete Fee Bypass for V3 Transactions - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The StarkNet OS Cairo program does not enforce a minimum `max_price_per_unit` on any resource bound for V3 transactions. Because `assert_nn` (used in `pack_resource_bounds`) only checks non-negativity (i.e., `>= 0`), a user can set all `max_price_per_unit` fields to `0` and `tip` to `0`. This causes `compute_max_possible_fee` to return `0`, which causes `charge_fee` to return immediately without executing any ERC-20 transfer. The transaction executes with a full gas budget but pays zero fees. The OS proof is valid and accepted by L1, making this a protocol-level bypass, not merely a mempool-level issue.

---

### Finding Description

**Step 1 — `pack_resource_bounds` allows `max_price_per_unit = 0`**

In `transaction_hash/transaction_hash.cairo`, the only validation on `max_price_per_unit` is:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);   // only checks >= 0; 0 is valid
    ...
}
```

`assert_nn(x)` asserts `x >= 0` in the field. The value `0` satisfies this check. There is no `assert_nn_le(..., 0)` lower-bound guard or any `minPricePerUnit` constant enforced here. [1](#0-0) 

Similarly, `tip` is validated only with `assert_nn_le(tip, 2 ** 64 - 1)`, which allows `tip = 0`. [2](#0-1) 

**Step 2 — `compute_max_possible_fee` returns 0 when all prices are 0**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
         + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
         + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
}
```

If `max_price_per_unit = 0` for all three resource bounds and `tip = 0`, this expression evaluates to `0` regardless of how large `max_amount` is. The user retains a full gas budget for execution while the computed fee ceiling is zero. [3](#0-2) 

**Step 3 — `charge_fee` unconditionally skips fee collection when `max_fee == 0`**

```cairo
func charge_fee(...)(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    ...
    let max_fee = compute_max_possible_fee(tx_info=tx_info);
    if (max_fee == 0) {
        return ();          // <-- fee transfer is never executed
    }
    ...
    assert_nn_le(calldata.amount.low, max_fee);   // never reached
    ...
}
```

The early return at line 123–125 means the ERC-20 `transfer` to the sequencer is never called. No fee is deducted from the sender's balance. [4](#0-3) 

`charge_fee` is called for every account transaction type: invoke, declare, and deploy_account. [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Impact: High — Network not being able to confirm new transactions (total network shutdown)**

Because the OS is the Cairo program that generates the STARK proof submitted to L1, a transaction that passes OS validation with zero fees produces a **provably valid proof**. The L1 verifier accepts it. This is not a mempool-level issue that a sequencer can filter away; the OS itself is the ground truth for what is a valid state transition.

An attacker can:
1. Craft V3 invoke transactions with `max_price_per_unit = 0` for all resource bounds, `tip = 0`, and a large `max_amount` (e.g., matching `L1_HANDLER_L2_GAS_MAX_AMOUNT = 100,000,000`).
2. Submit them to the network at zero cost.
3. Flood the network with computationally expensive but economically free transactions.
4. Crowd out legitimate fee-paying transactions, preventing the network from confirming them.

Since the OS proof is valid, any sequencer (including a misconfigured or economically rational one that accepts zero-fee transactions to fill blocks) will produce proofs that L1 accepts, cementing the zero-fee state transitions on-chain.

---

### Likelihood Explanation

Any unprivileged user can craft a valid V3 transaction with all-zero prices. The transaction hash computation in `hash_fee_fields` / `pack_resource_bounds` accepts these values without error. The OS will execute the transaction and produce a valid proof. The only external gate is the sequencer's mempool policy, but the OS itself provides no enforcement, meaning a single misconfigured or adversarial sequencer is sufficient to exploit this at scale.

---

### Recommendation

Introduce a `MIN_PRICE_PER_UNIT` constant (or a configurable `min_price_per_unit` in `BlockContext`) and enforce it inside `pack_resource_bounds` or at the start of `charge_fee`:

```cairo
// In pack_resource_bounds or hash_fee_fields:
with_attr error_message("Price per unit below minimum.") {
    assert_nn_le(MIN_PRICE_PER_UNIT, resource_bounds.max_price_per_unit);
}
```

Alternatively, enforce a non-zero `compute_max_possible_fee` result before allowing execution to proceed:

```cairo
// At the start of charge_fee or before execute_invoke_function_transaction proceeds:
with_attr error_message("Transaction max fee must be non-zero.") {
    assert_not_zero(max_fee);
}
```

The bootstrap path (which legitimately uses zero-fee transactions) already has its own guarded code path (`sender_address == 'BOOTSTRAP' and tx_info.nonce == 0`) and should be exempted explicitly. [8](#0-7) 

---

### Proof of Concept

1. Construct a V3 invoke transaction with:
   - `resource_bounds[L1_GAS].max_price_per_unit = 0`, `max_amount = 1_000_000`
   - `resource_bounds[L2_GAS].max_price_per_unit = 0`, `max_amount = 100_000_000`
   - `resource_bounds[L1_DATA_GAS].max_price_per_unit = 0`, `max_amount = 1_000_000`
   - `tip = 0`

2. Submit to the sequencer. The OS processes it through `execute_invoke_function_transaction` → `get_account_tx_common_fields` → `compute_invoke_transaction_hash` (which calls `pack_resource_bounds` — passes because `assert_nn(0)` succeeds).

3. `charge_fee` is called. `compute_max_possible_fee` returns `0 * 0 + 100_000_000 * (0 + 0) + 0 * 0 = 0`.

4. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any tokens.

5. The transaction executes with a 100M L2 gas budget, the sequencer receives zero fee, and the OS produces a valid STARK proof accepted by L1.

6. Repeat at scale with zero marginal cost to spam the network.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L116-117)
```text
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-125)
```text
    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L686-688)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=validate_deploy_execution_context);

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
