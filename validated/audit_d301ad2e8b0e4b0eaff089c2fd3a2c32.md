### Title
`starknet_estimateFee` Returns `overall_fee` Including L2 Gas Costs But Omits `l2_gas_consumed`, Producing an Authoritative-Looking Inconsistent Fee Estimation — (File: `crates/apollo_rpc_execution/src/objects.rs`)

---

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` includes `overall_fee` that correctly incorporates L2 gas costs, but omits the `l2_gas_consumed` field entirely. The response exposes `l2_gas_price` without the corresponding consumed amount, making the `overall_fee` irreconcilable with the other returned fields for any V3 transaction that consumes L2 gas. Callers receive an authoritative-looking but internally inconsistent fee breakdown.

---

### Finding Description

In `crates/apollo_rpc_execution/src/objects.rs`, the `FeeEstimation` struct is defined as:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,        // L1 gas only
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,   // L1 data gas only
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of
    // l1_gas_price only is close enough (as there are roundings) to the fee
    // of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,    // price present, consumed amount absent
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The constructor `tx_execution_output_to_fee_estimation` populates `overall_fee` from `tx_execution_output.execution_info.receipt.fee`, which is the full fee including L2 gas, but only maps `gas_vector.l1_gas` and `gas_vector.l1_data_gas` into the response — `gas_vector.l2_gas` is silently dropped:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
``` [2](#0-1) 

The OpenRPC schema for `FEE_ESTIMATE` describes `overall_fee` as "equals to `gas_consumed*gas_price + data_gas_consumed*data_gas_price`": [3](#0-2) 

For any V3 transaction that consumes L2 gas (i.e., any transaction using `AllResources` bounds), the actual `overall_fee` equals:

```
L1_gas * L1_price + L1_data_gas * L1_data_price + L2_gas * L2_price
```

But the response only exposes `L1_gas` and `L1_data_gas`. The caller receives `l2_gas_price` with no corresponding `l2_gas_consumed`, so:

- `overall_fee` **cannot be reconstructed** from the other fields in the response.
- The formula stated in the spec is **wrong** for V3 transactions.
- The caller cannot determine the correct `l2_gas.max_amount` to set in their transaction's resource bounds.

This is confirmed by the transaction prover's own RPC records, which show `l2_gas_consumed` as a non-zero value in simulation responses (e.g., `"l2_gas_consumed": "0xb56b6"`), demonstrating that L2 gas is a real, non-trivial component of the fee: [4](#0-3) 

---

### Impact Explanation

**Impact: High — RPC fee estimation returns an authoritative-looking wrong value.**

The `starknet_estimateFee` RPC endpoint is the canonical source of truth for fee estimation. Wallets and dApps use its response to set `l2_gas.max_amount` in V3 transactions. Because `l2_gas_consumed` is absent:

1. The `overall_fee` is inconsistent with the formula `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` — the difference is the hidden L2 gas cost.
2. A caller who trusts the spec formula will compute a fee lower than `overall_fee`, leading to confusion about why the actual fee is higher.
3. A caller who tries to set `l2_gas.max_amount` from the estimation cannot do so correctly without reverse-engineering it from `(overall_fee - l1_cost - l1_data_cost) / l2_gas_price`, which is subject to rounding and requires knowing all three prices simultaneously.
4. If the caller sets `l2_gas.max_amount` too low (based on the incomplete response), the transaction will revert with an out-of-gas error at execution time, even though the fee estimation appeared to succeed.

---

### Likelihood Explanation

**Likelihood: High.**

- Every V3 transaction (`version=0x3`) with `AllResources` bounds that executes Cairo 1 code consumes L2 (Sierra) gas. This is the standard transaction type for Starknet post-0.13.x.
- The `starknet_estimateFee` endpoint is called by every wallet and dApp before submitting a transaction.
- The missing field is acknowledged in a `TODO` comment in the source, confirming the developers are aware of the gap.
- The RPC API is the primary interface for all external callers; there is no alternative authoritative source for `l2_gas_consumed` per-transaction.

---

### Recommendation

Add `l2_gas_consumed: Felt` to the `FeeEstimation` struct and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`. Update the OpenRPC schema to include `l2_gas_consumed` and `l2_gas_price` as required fields, and correct the `overall_fee` description to reflect the three-component formula. This directly resolves the acknowledged `TODO(Tzahi)` in the source.

---

### Proof of Concept

1. Submit a V3 invoke transaction to `starknet_estimateFee` against a block with `starknet_version >= 0.13.x`.
2. Observe the response: `overall_fee = F`, `gas_consumed = G1`, `data_gas_consumed = G2`, `l1_gas_price = P1`, `l1_data_gas_price = P2`, `l2_gas_price = P3`.
3. Compute `reconstructed_fee = G1 * P1 + G2 * P2`. For any transaction consuming L2 gas, `reconstructed_fee < F`.
4. The difference `F - reconstructed_fee = L2_gas_consumed * P3` is the hidden L2 gas cost, which is non-zero for all Cairo 1 V3 transactions (as confirmed by the prover's own simulation records showing `l2_gas_consumed = 0xb56b6`).
5. A caller who sets `l2_gas.max_amount` based on the incomplete response (e.g., using `reconstructed_fee / P3 = 0`) will have their transaction revert with "Out of gas" at execution time, despite the fee estimation having succeeded. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L94-113)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct FeeEstimation {
    /// Gas consumed by this transaction. This includes gas for DA in calldata mode.
    pub gas_consumed: Felt,
    /// The gas price for execution and calldata DA.
    pub l1_gas_price: GasPrice,
    /// Gas consumed by DA in blob mode.
    pub data_gas_consumed: Felt,
    /// The gas price for DA blob.
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    /// The L2 gas price for execution.
    pub l2_gas_price: GasPrice,
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    /// The unit in which the fee was paid (Wei/Fri).
    pub unit: PriceUnit,
}
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L161-183)
```rust
pub(crate) fn tx_execution_output_to_fee_estimation(
    tx_execution_output: &TransactionExecutionOutput,
    block_context: &BlockContext,
) -> ExecutionResult<FeeEstimation> {
    let gas_prices = &block_context.block_info().gas_prices;
    let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
        gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
    );

    let gas_vector = tx_execution_output.execution_info.receipt.gas;

    Ok(FeeEstimation {
        gas_consumed: gas_vector.l1_gas.0.into(),
        l1_gas_price,
        data_gas_consumed: gas_vector.l1_data_gas.0.into(),
        l1_data_gas_price,
        l2_gas_price,
        overall_fee: tx_execution_output.execution_info.receipt.fee,
        unit: tx_execution_output.price_unit,
    })
}
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3666)
```json
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "unit": {
                        "title": "Fee unit",
                        "description": "units in which the fee is given",
                        "$ref": "#/components/schemas/PRICE_UNIT"
                    }
                },
                "required": [
                    "gas_consumed",
                    "l1_gas_price",
                    "data_gas_consumed",
                    "l1_data_gas_price",
                    "overall_fee",
                    "unit"
                ]
```

**File:** crates/starknet_transaction_prover/resources/rpc_records/test_simulate_and_get_initial_reads.json (L91-101)
```json
            {
              "fee_estimation": {
                "l1_data_gas_consumed": "0x80",
                "l1_data_gas_price": "0x3e8",
                "l1_gas_consumed": "0x0",
                "l1_gas_price": "0xe8d4a51000",
                "l2_gas_consumed": "0xb56b6",
                "l2_gas_price": "0x1dcd65000",
                "overall_fee": "0x151eb86f3ed400",
                "unit": "FRI"
              },
```
