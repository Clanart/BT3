### Title
`FeeEstimation` RPC Response Omits `l2_gas_consumed`, Breaking the `overall_fee` Invariant in `starknet_estimateFee` and `starknet_simulateTransactions` - (File: crates/apollo_rpc_execution/src/objects.rs)

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` documents the invariant `overall_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`. For V3 transactions using `AllResources` bounds, this invariant is broken: `overall_fee` is computed from all three gas dimensions (L1, L1-data, L2), but `l2_gas_consumed` is never populated in the response. The RPC response is internally inconsistent, and the codebase itself acknowledges this with an unresolved TODO.

### Finding Description

`tx_execution_output_to_fee_estimation` in `crates/apollo_rpc_execution/src/objects.rs` constructs the `FeeEstimation` object returned to RPC callers:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),       // L1 gas only
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(), // L1 data gas only
    l1_data_gas_price,
    l2_gas_price,                                   // price exposed, but...
    overall_fee: tx_execution_output.execution_info.receipt.fee, // includes L2 gas cost
    unit: tx_execution_output.price_unit,
})
``` [1](#0-0) 

The `overall_fee` is taken from `receipt.fee`, which is computed by `GasVector::cost()`:

```rust
for (gas, price, resource) in [
    (self.l1_gas,      gas_prices.l1_gas_price,      Resource::L1Gas),
    (self.l1_data_gas, gas_prices.l1_data_gas_price,  Resource::L1DataGas),
    (self.l2_gas,      tipped_l2_gas_price,           Resource::L2Gas),  // ← included
] { ... }
``` [2](#0-1) 

So `overall_fee = l1_gas * l1_gas_price + l1_data_gas * l1_data_gas_price + l2_gas * (l2_gas_price + tip)`.

But the `FeeEstimation` struct's documented invariant states:

```rust
/// The total amount of fee. This is equal to:
/// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
``` [3](#0-2) 

The L2 gas component is silently included in `overall_fee` but the `l2_gas_consumed` field is absent. The codebase acknowledges this with an unresolved TODO:

```rust
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
``` [4](#0-3) 

The RPC OpenAPI schema reinforces the broken invariant:

```json
"overall_fee": {
    "description": "...equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price"
}
``` [5](#0-4) 

This is the exact analog of the Umee report's invariant #5 and #8: a fee/resource accounting invariant that is documented but not enforced, and a public-facing value that cannot be independently verified from the other fields in the same response.

### Impact Explanation

Any caller of `starknet_estimateFee` or `starknet_simulateTransactions` for a V3 transaction with `AllResources` bounds and non-zero L2 gas receives a `FeeEstimation` where:

- `overall_fee` is **correct** (includes L2 gas cost)
- `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` is **less than** `overall_fee`

The response is internally inconsistent. A client that attempts to verify the fee using the documented formula will compute a value lower than `overall_fee`, causing:

1. **Wrong fee budget planning**: wallets and dApps that reconstruct the fee from the decomposed fields will underestimate the actual charge.
2. **Unverifiable fee**: the `l2_gas_price` field is exposed but its corresponding consumed amount is absent, making the response misleading — it implies L2 gas is priced but not consumed.
3. **Broken proof input**: any off-chain component (e.g., a transaction prover or SNOS input builder) that reconstructs the fee from the RPC response fields will produce a value inconsistent with the on-chain `receipt.fee`, potentially causing proof verification failures.

This matches the allowed impact: **High — RPC fee estimation returns an authoritative-looking wrong value** (the decomposition is wrong even though `overall_fee` itself is numerically correct).

### Likelihood Explanation

- Trigger requires only a standard V3 (`INVOKE` version 3) transaction with `AllResources` resource bounds, which is the current production transaction format on Starknet.
- No special privileges are needed; any user can call `starknet_estimateFee`.
- The discrepancy grows proportionally with L2 gas consumed, which is non-trivial for any Cairo 1 contract execution.
- The TODO comment confirms the issue is known but unresolved.

### Recommendation

1. Add `l2_gas_consumed: Felt` to the `FeeEstimation` struct and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Update the struct's doc comment and the OpenAPI schema description to reflect the correct three-term formula: `overall_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price + l2_gas_consumed * l2_gas_price`.
3. Add an invariant assertion (or at minimum a test) that verifies `overall_fee == gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price + l2_gas_consumed * l2_gas_price` for all returned `FeeEstimation` objects.

### Proof of Concept

1. Submit a V3 `INVOKE` transaction with `AllResources` bounds to a node running this codebase.
2. Call `starknet_estimateFee` for that transaction.
3. From the response, compute: `reconstructed = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`.
4. Observe: `reconstructed < overall_fee` whenever the transaction consumes non-zero L2 gas (i.e., any Cairo 1 contract call).
5. The difference equals `l2_gas * l2_gas_price`, which is silently absorbed into `overall_fee` but invisible in the response fields.

The existing test `call_simulate_skip_fee_charge` in `crates/apollo_rpc/src/v0_8/execution_test.rs` asserts `fee_estimation == *EXPECTED_FEE_ESTIMATE` but does not verify the internal consistency invariant `overall_fee == gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`, confirming the invariant is untested. [6](#0-5)

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L104-113)
```rust
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

**File:** crates/starknet_api/src/execution_resources.rs (L166-186)
```rust
        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3652)
```json
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
                    },
```

**File:** crates/apollo_rpc/src/v0_8/execution_test.rs (L713-760)
```rust
#[tokio::test]
async fn call_simulate_skip_fee_charge() {
    let (module, storage_writer) = get_test_rpc_server_and_storage_writer::<JsonRpcServerImpl>();

    prepare_storage_for_execution(storage_writer);

    let invoke = BroadcastedTransaction::Invoke(InvokeTransaction::Version1(InvokeTransactionV1 {
        max_fee: Fee(1000000 * GAS_PRICE.price_in_wei.0),
        version: TransactionVersion1::Version1,
        sender_address: *ACCOUNT_ADDRESS,
        calldata: calldata![
            *DEPRECATED_CONTRACT_ADDRESS.0.key(),  // Contract address.
            selector_from_name("return_result").0, // EP selector.
            felt!(1_u8),                           // Calldata length.
            felt!(2_u8)                            // Calldata: num.
        ],
        ..Default::default()
    }));

    let mut res = call_and_validate_schema_for_result::<_, Vec<SimulatedTransaction>>(
        &module,
        "starknet_V0_8_simulateTransactions",
        vec![
            Box::new(BlockId::HashOrNumber(BlockHashOrNumber::Number(BlockNumber(0)))),
            Box::new(vec![invoke]),
            Box::new(vec![SimulationFlag::SkipFeeCharge]),
        ],
        &VERSION,
        SpecFile::TraceApi,
    )
    .await;

    assert_eq!(res.len(), 1);

    let simulated_tx = res.pop().unwrap();

    assert_eq!(simulated_tx.fee_estimation, *EXPECTED_FEE_ESTIMATE);

    assert_matches!(simulated_tx.transaction_trace, TransactionTrace::Invoke(_));

    let TransactionTrace::Invoke(invoke_trace) = simulated_tx.transaction_trace else {
        unreachable!();
    };

    assert_matches!(invoke_trace.validate_invocation, Some(_));
    assert_matches!(invoke_trace.execute_invocation, FunctionInvocationResult::Ok(_));
    assert_matches!(invoke_trace.fee_transfer_invocation, None);
}
```
