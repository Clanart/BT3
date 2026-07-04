### Title
Zero `max_price_per_unit` in Resource Bounds Allows Fee-Free Transaction Execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`pack_resource_bounds` validates `max_price_per_unit` with `assert_nn` (non-negative only), not `assert_not_zero`. A user can set all three resource-bound prices to zero, making `compute_max_possible_fee` return 0, which causes `charge_fee` to skip the ERC-20 transfer entirely. The transaction executes with full gas but pays no fee — a direct analog to the `amountAMin = 0` / `amountBMin = 0` zero-protection bug.

---

### Finding Description

In `pack_resource_bounds`, the only constraint on `max_price_per_unit` is:

```cairo
assert_nn(resource_bounds.max_price_per_unit);
```

`assert_nn` checks that the value is in the range `[0, P/2]` (non-negative in Cairo's felt arithmetic). It does **not** reject zero. [1](#0-0) 

`compute_max_possible_fee` computes the fee as a sum of products:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

If all three `max_price_per_unit` values are 0 and `tip` is 0, this expression evaluates to 0 regardless of `max_amount`.

`charge_fee` then short-circuits immediately:

```cairo
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

No ERC-20 transfer is executed. The sequencer receives nothing.

Meanwhile, `get_initial_user_gas_bound` returns `resource_bounds[L2_GAS_INDEX].max_amount`, so the attacker can still set a large L2 gas amount to obtain full execution gas while paying zero price per unit. [4](#0-3) 

This affects all three V3 account transaction types: `execute_invoke_function_transaction` (line 361), `execute_declare_transaction` (line 822), and `execute_deploy_account_transaction`. [5](#0-4) 

---

### Impact Explanation

**Direct loss of funds (Critical):** The sequencer expends real computational resources (L2 gas, L1 data availability, L1 settlement) executing the transaction but receives zero fee. At scale, this drains sequencer economics entirely.

**Network shutdown (High):** Because transactions are free, an attacker can flood the network with zero-cost transactions at maximum gas, exhausting sequencer capacity and preventing legitimate transactions from being confirmed — a total network halt.

---

### Likelihood Explanation

Any unprivileged transaction sender can craft a valid V3 transaction with all `max_price_per_unit = 0` and `tip = 0`. The transaction hash computation in `hash_fee_fields` / `pack_resource_bounds` accepts these values without rejection, so the transaction is cryptographically valid and will be accepted by the OS. No special privilege, leaked key, or external dependency is required. [6](#0-5) 

---

### Recommendation

Replace `assert_nn` with `assert_not_zero` (or `assert_nn_le` with a protocol-defined minimum price floor) in `pack_resource_bounds`:

```cairo
// Before (vulnerable):
assert_nn(resource_bounds.max_price_per_unit);

// After (fixed):
assert_not_zero(resource_bounds.max_price_per_unit);
```

Additionally, `charge_fee` should assert that `max_fee != 0` for non-bootstrap V3 account transactions rather than silently returning. [7](#0-6) 

---

### Proof of Concept

1. Construct a V3 invoke transaction with:
   - `l2_gas_bounds = ResourceBounds(resource=L2_GAS, max_amount=10_000_000, max_price_per_unit=0)`
   - `l1_gas_bounds = ResourceBounds(resource=L1_GAS, max_amount=0, max_price_per_unit=0)`
   - `l1_data_gas_bounds = ResourceBounds(resource=L1_DATA_GAS, max_amount=0, max_price_per_unit=0)`
   - `tip = 0`

2. `pack_resource_bounds` passes for all three bounds — `assert_nn(0)` succeeds. [1](#0-0) 

3. `compute_max_possible_fee` returns `0 * 0 + 10_000_000 * (0 + 0) + 0 * 0 = 0`. [8](#0-7) 

4. `charge_fee` hits `if (max_fee == 0) { return (); }` — no fee is charged. [3](#0-2) 

5. `get_initial_user_gas_bound` returns `10_000_000` — the transaction executes with full gas at zero cost. [9](#0-8)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L110-144)
```text
func hash_fee_fields{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    tip: felt, resource_bounds: ResourceBounds*, n_resource_bounds: felt
) -> felt {
    alloc_locals;

    let (local data_to_hash: felt*) = alloc();
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);

    static_assert L1_GAS_INDEX == 0;
    static_assert L2_GAS_INDEX == 1;
    static_assert L1_DATA_GAS_INDEX == 2;

    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
    }

    // L1 gas.
    let l1_gas_bounds = resource_bounds[L1_GAS_INDEX];
    assert l1_gas_bounds.resource = L1_GAS;
    assert data_to_hash[1] = pack_resource_bounds(l1_gas_bounds);

    // L2 gas.
    let l2_gas_bounds = resource_bounds[L2_GAS_INDEX];
    assert l2_gas_bounds.resource = L2_GAS;
    assert data_to_hash[2] = pack_resource_bounds(l2_gas_bounds);

    // L1 data gas.
    let l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];
    assert l1_data_gas_bounds.resource = L1_DATA_GAS;
    assert data_to_hash[3] = pack_resource_bounds(l1_data_gas_bounds);

    let (hash) = poseidon_hash_many(n=n_resource_bounds + 1, elements=data_to_hash);
    return hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L123-125)
```text
    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L322-323)
```text
    let initial_user_gas_bound = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    let remaining_gas = initial_user_gas_bound;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```
