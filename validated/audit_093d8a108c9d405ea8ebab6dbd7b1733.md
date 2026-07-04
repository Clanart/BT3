### Title
`tx_info.tip` Treated as Per-Unit Price Instead of Flat Fee in `compute_max_possible_fee`, Inflating the Fee Cap - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary
In `compute_max_possible_fee`, the transaction `tip` field — a flat total fee amount in fri — is incorrectly added to `l2_gas_bounds.max_price_per_unit` (a per-unit price in fri/gas) and then multiplied by `l2_gas_bounds.max_amount`. This is a direct unit mismatch analogous to H-5: a value of one unit type is used as if it were a different unit type. The result is that the OS-enforced fee cap is inflated by `tip × (l2_gas_max_amount − 1)`, allowing the sequencer to charge far more than the user authorized.

### Finding Description

In `transaction_impls.cairo`, `compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

This expands to:

```
l1_gas_max_amount * l1_gas_max_price
+ l2_gas_max_amount * l2_gas_max_price
+ l2_gas_max_amount * tip          ← tip scaled by gas amount
+ l1_data_gas_max_amount * l1_data_gas_max_price
```

The correct formula per the StarkNet v3 transaction specification (SNIP-8) is:

```
sum(max_amount_i * max_price_per_unit_i) + tip   ← tip added once as a flat amount
```

The `tip` field is a flat priority fee in fri (bounded to 64 bits), confirmed by `hash_fee_fields` which hashes `tip` as a standalone scalar, completely separate from the per-unit `resource_bounds` entries: [2](#0-1) 

Meanwhile `max_price_per_unit` is a 128-bit per-unit price (fri/gas), packed as such in `pack_resource_bounds`: [3](#0-2) 

Adding a flat-fee scalar to a per-unit price and then multiplying by a gas amount is the same class of unit mismatch as H-5 (token amount added to a USD value). The result is that `max_fee` is inflated by exactly `tip × (l2_gas_max_amount − 1)`.

The inflated `max_fee` is then used as the sole upper bound on the actual fee charged:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [4](#0-3) 

The actual fee is loaded from the sequencer-controlled hint `LoadActualFee`: [5](#0-4) 

Because the OS proof enforces only `actual_fee ≤ max_fee`, and `max_fee` is computed incorrectly, the proof remains valid even when the sequencer charges `tip × (l2_gas_max_amount − 1)` more than the user authorized.

### Impact Explanation

**Critical — Direct loss of funds.**

The user's signature commits to `tip` and `resource_bounds` (via `hash_fee_fields` → `hash_tx_common_fields` → transaction hash). The user's intent is that the OS enforces `actual_fee ≤ sum(max_amount_i * max_price_i) + tip`. Due to the bug, the OS instead enforces `actual_fee ≤ sum(max_amount_i * max_price_i) + l2_gas_max_amount * tip`.

For a realistic transaction with `l2_gas_max_amount = 1,000,000` and `tip = 1,000 fri`, the sequencer can charge up to `999,999,000 fri` more than the user authorized — while producing a valid STARK proof. The user has no on-chain recourse because the proof verifies correctly.

### Likelihood Explanation

Every V3 transaction with a non-zero `tip` and non-zero `l2_gas_max_amount` is affected. The sequencer controls the `LoadActualFee` hint and can set `low_actual_fee` to any value up to the inflated `max_fee`. The OS proof will accept it. No special access beyond operating the sequencer is required to exploit this once the bug is present in the proven OS program.

### Recommendation

Add `tip` as a flat addend after summing the per-resource fee products:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * l2_gas_bounds.max_price_per_unit
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit
     + tx_info.tip;
```

### Proof of Concept

1. User submits a V3 invoke transaction with:
   - `l2_gas_max_amount = 1_000_000`
   - `l2_gas_max_price_per_unit = 100` fri/gas
   - `tip = 1_000` fri
   - `l1_gas_max_amount = 0`, `l1_data_gas_max_amount = 0`

2. Correct `max_fee` = `1_000_000 * 100 + 1_000` = `100_001_000` fri.

3. Buggy `max_fee` = `1_000_000 * (100 + 1_000)` = `1_100_000_000` fri.

4. Sequencer sets `LoadActualFee` hint to `1_100_000_000`.

5. `assert_nn_le(1_100_000_000, 1_100_000_000)` passes. The ERC-20 transfer of `1_100_000_000` fri executes. The proof is valid.

6. User loses `999_999_000` fri beyond what they authorized — with a valid proof that cannot be disputed on L1.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-128)
```text
    local low_actual_fee;
    %{ LoadActualFee %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-107)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L115-117)
```text
    let (local data_to_hash: felt*) = alloc();
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);
```
