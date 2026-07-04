### Title
Unchecked Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Zero-Fee Transaction Execution — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in `transaction_impls.cairo` performs multiplication and addition of user-controlled resource bound values using raw felt arithmetic, with no range checks on the inputs. Because Cairo felt arithmetic is modular (mod the Stark prime P ≈ 2²⁵¹), an attacker can craft resource bound values such that the sum wraps to exactly 0 mod P. The `charge_fee` function contains an explicit early-return guard `if (max_fee == 0) { return (); }` that skips all fee collection when this occurs, allowing the transaction to execute with zero fee paid to the sequencer.

---

### Finding Description

**Root cause — `compute_max_possible_fee` (lines 87–102):** [1](#0-0) 

The function returns:

```
l1_gas_max_amount * l1_gas_max_price
+ l2_gas_max_amount * (l2_gas_max_price + tip)
+ l1_data_gas_max_amount * l1_data_gas_max_price
```

All six operands (`max_amount`, `max_price_per_unit` for each of the three resource types, plus `tip`) are felt values loaded directly from the user-supplied transaction via the hint `%{ LoadCommonTxFields %}` in `get_account_tx_common_fields`. [2](#0-1) 

No range-check assertions constrain these values to any sub-field range (e.g., u64 for `max_amount`, u128 for `max_price_per_unit`) before they are multiplied together. The multiplication and addition are therefore performed modulo P.

**Trigger — `charge_fee` early-return guard (lines 123–125):** [3](#0-2) 

When `max_fee == 0`, the function returns immediately without executing the ERC-20 transfer to the sequencer. There is no assertion that `max_fee` must be non-zero for a non-trivial transaction.

**Concrete overflow construction:**

The Stark prime is P = 2²⁵¹ + 17·2¹⁹² + 1. An attacker can trivially choose:

- `l1_gas_max_amount = 1`, `l1_gas_max_price = P − 1` → term₁ = P − 1
- `l2_gas_max_amount = 1`, `l2_gas_max_price = 0`, `tip = 0` → term₂ = 0
- `l1_data_gas_max_amount = 1`, `l1_data_gas_max_price = 1` → term₃ = 1

Sum = (P − 1) + 0 + 1 = P ≡ 0 (mod P).

The user signs over these values as part of the transaction hash (computed in `compute_invoke_transaction_hash`), so the signature is valid. The OS verifies the hash but does not range-check the individual resource bound fields before passing them to `compute_max_possible_fee`. [4](#0-3) 

**Fee collection is then skipped entirely:** [5](#0-4) 

The same pattern applies to `execute_deploy_account_transaction` and `execute_declare_transaction`, both of which call `charge_fee` through the same path. [6](#0-5) 

---

### Impact Explanation

**Direct loss of funds (Critical):** The sequencer expends real computational resources (Cairo VM steps, builtins, L1 data availability) to execute and prove the transaction, but receives zero fee. The ERC-20 transfer to `sequencer_address` is skipped entirely. An attacker can repeat this for every transaction type (invoke, deploy_account, declare), draining sequencer revenue without limit.

**Network halt (High):** Because execution is free, an attacker can flood the network with zero-cost transactions consuming the full `EXECUTE_MAX_SIERRA_GAS = 1_100_000_000` gas budget per transaction. [7](#0-6) 

This saturates block capacity and prevents legitimate transactions from being confirmed, constituting a total network shutdown.

---

### Likelihood Explanation

The attacker is an ordinary transaction sender. No privileged role, leaked key, or external dependency is required. The attacker controls all resource bound fields and the tip, which are user-supplied felt values included in the transaction. Constructing a set of values whose product-sum equals P mod P is trivial arithmetic. The attack is repeatable, cheap (no on-chain cost), and deterministic.

---

### Recommendation

1. **Range-check all resource bound fields** before using them in `compute_max_possible_fee`. Assert that `max_amount` fits in u64 (≤ 2⁶⁴ − 1) and `max_price_per_unit` fits in u128 (≤ 2¹²⁸ − 1) using `assert_nn_le`. With these bounds, the maximum possible sum is bounded by 3 · (2⁶⁴ − 1) · (2¹²⁸ − 1) < 3 · 2¹⁹² ≪ P, making overflow impossible.

2. **Assert `max_fee > 0`** for any transaction that actually consumes resources, rather than silently skipping fee collection.

3. Apply the same range checks to `tip` (which is also added to `l2_gas_max_price` before multiplication).

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas: { max_amount: 1, max_price_per_unit: P−1 }`
   - `l2_gas: { max_amount: 1, max_price_per_unit: 0 }`, `tip: 0`
   - `l1_data_gas: { max_amount: 1, max_price_per_unit: 1 }`
   - Any valid calldata targeting any contract

2. Attacker signs the transaction hash (which commits to these resource bounds).

3. Sequencer includes the transaction in a block.

4. OS executes `compute_max_possible_fee`:
   - `(1 · (P−1)) + (1 · (0+0)) + (1 · 1) = P ≡ 0 (mod P)`

5. `charge_fee` sees `max_fee == 0` and returns immediately. [8](#0-7) 

6. The transaction executes fully (up to `EXECUTE_MAX_SIERRA_GAS`) with zero fee paid. The proof is valid because the OS never asserts that `max_fee` must be non-zero.

7. Attacker repeats indefinitely, executing arbitrary computation for free and exhausting block capacity.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L174-197)
```text
    local resource_bounds: ResourceBounds*;
    local tip;
    local paymaster_data_length;
    local paymaster_data: felt*;
    local nonce_data_availability_mode;
    local fee_data_availability_mode;
    local nonce;
    %{ LoadCommonTxFields %}
    %{ LoadTxNonceAccount %}
    tempvar common_tx_fields = new CommonTxFields(
        tx_hash_prefix=tx_hash_prefix,
        version=3,
        sender_address=sender_address,
        chain_id=block_context.os_global_context.starknet_os_config.chain_id,
        nonce=nonce,
        tip=tip,
        n_resource_bounds=3,
        resource_bounds=resource_bounds,
        paymaster_data_length=paymaster_data_length,
        paymaster_data=paymaster_data,
        nonce_data_availability_mode=nonce_data_availability_mode,
        fee_data_availability_mode=fee_data_availability_mode,
    );
    return common_tx_fields;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L283-294)
```text
    let poseidon_ptr = builtin_ptrs.selectable.poseidon;
    with poseidon_ptr {
        let transaction_hash = compute_invoke_transaction_hash(
            common_fields=common_tx_fields,
            execution_context=tx_execution_context,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
            proof_facts_size=proof_facts_size,
            proof_facts=proof_facts,
        );
    }
    update_poseidon_in_builtin_ptrs(poseidon_ptr=poseidon_ptr);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L91-91)
```text
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
```
