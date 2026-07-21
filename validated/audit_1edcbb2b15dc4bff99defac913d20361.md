### Title
`starknet_estimateFee` Returns Artificially Low L2/L1-Data Gas Price via Stale Pending Fallback — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When `starknet_estimateFee` (or `starknet_simulateTransactions`) is called with `block_id = "pending"` and the in-memory pending data is stale (its parent hash does not match the latest committed block), the RPC constructs a synthetic `DeprecatedPendingBlock` fallback that omits `l1_data_gas_price` and `l2_gas_price`. The execution layer then silently substitutes `NonzeroGasPrice::MIN` (1 fri) for both fields. The returned `FeeEstimation` carries an authoritative-looking `l2_gas_price` that is ~10⁹× below the real next-block price, causing any transaction whose `max_price_per_unit` is set from this estimate to fail at sequencing time with `MaxGasPriceTooLow`.

---

### Finding Description

**Step 1 — Stale-pending fallback omits L2 gas price.**

`read_pending_data` checks whether the cached pending block's parent hash equals the latest committed block hash. When they differ (a normal, frequent condition between block commits), it constructs a `DeprecatedPendingBlock`:

```rust
Ok(PendingData {
    block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
        parent_block_hash: latest_header.block_hash,
        eth_l1_gas_price: latest_header...l1_gas_price.price_in_wei,
        strk_l1_gas_price: latest_header...l1_gas_price.price_in_fri,
        timestamp: ...,
        sequencer_address: ...,
        starknet_version: ...,
        ..Default::default()   // l1_data_gas_price and l2_gas_price are ZERO
    }),
    ...
})
``` [1](#0-0) 

`DeprecatedPendingBlock` has no `l1_data_gas_price` or `l2_gas_price` fields; its `l2_gas_price()` accessor returns `GasPricePerToken::default()` (zero): [2](#0-1) 

**Step 2 — Execution layer silently replaces zero with `NonzeroGasPrice::MIN`.**

`create_block_context_for_execution` (called by `exec_estimate_fee`) reads the pending data's gas prices and substitutes `NonzeroGasPrice::MIN` whenever a price is zero:

```rust
l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),
``` [3](#0-2) 

`NonzeroGasPrice::MIN` is 1 fri. The real L2 gas price in production is on the order of 1 Gwei (10⁹ fri).

**Step 3 — Wrong price propagates into the authoritative `FeeEstimation` response.**

`tx_execution_output_to_fee_estimation` reads the gas prices directly from the `BlockContext` built in Step 2 and places them in the RPC response:

```rust
let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
    gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
    gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
    gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
);
``` [4](#0-3) 

The returned `FeeEstimation.l2_gas_price` is 1 fri and `overall_fee` is computed with that price, making the estimate appear valid but be ~10⁹× too low for the L2 gas component.

**Analog to the external bug.**

The external report describes a rate stored at pool level (not position level) that can be changed before a transaction is processed, causing the user to be charged at the wrong rate. Here, the gas price is stored at block level (not transaction level). When the pending data is stale, the block-level price used for estimation is wrong (1 fri instead of ~1 Gwei). The user's transaction is then submitted with a `max_price_per_unit` derived from the wrong estimate, and it fails at execution time — the sequencer-native analog of the borrower being charged at the manipulated rate.

---

### Impact Explanation

`starknet_estimateFee` with `block_id = "pending"` returns an authoritative-looking `FeeEstimation` whose `l2_gas_price` field is `NonzeroGasPrice::MIN` (1 fri) and whose `overall_fee` is computed from that price. A user who sets `resource_bounds.l2_gas.max_price_per_unit` to the returned `l2_gas_price` will have their transaction rejected by the blockifier with `ResourceBoundsError::MaxGasPriceTooLow` when the actual block gas price (~1 Gwei) is applied:

```rust
if resource_bounds.max_price_per_unit < actual_gas_price.get() {
    insufficiencies_resource.push(
        ResourceBoundsError::MaxGasPriceTooLow { ... }
    );
}
``` [5](#0-4) 

This matches the allowed impact: **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

---

### Likelihood Explanation

The stale-pending condition is triggered every time a block is committed before the pending data is refreshed — a normal, continuous occurrence in production. Any `estimateFee` call with `block_id = "pending"` during that window receives the wrong estimate. No privileged access or adversarial action is required; the condition is self-induced by normal block production.

---

### Recommendation

In the stale-pending fallback inside `read_pending_data`, use `PendingBlock` (the current format) instead of `DeprecatedPendingBlock`, and populate `l1_data_gas_price` and `l2_gas_price` from the latest committed block header (mirroring how `l1_gas_price` is already populated). Alternatively, compute the expected next-block L2 gas price via `calculate_next_l2_gas_price_for_fin` and include it in the synthetic pending block. [6](#0-5) 

---

### Proof of Concept

1. Commit block N to storage so that `latest_header.block_hash = H_N`.
2. Leave the in-memory `pending_data` with `parent_block_hash = H_{N-1}` (stale).
3. Call `starknet_estimateFee` with `block_id = "pending"` for a v3 `AllResources` transaction.
4. `read_pending_data` detects the hash mismatch and returns `DeprecatedPendingBlock` with `l2_gas_price = 0`.
5. `create_block_context_for_execution` substitutes `NonzeroGasPrice::MIN` (1 fri).
6. The returned `FeeEstimation.l2_gas_price` is 1 fri; `overall_fee` is ~10⁹× too low.
7. Submit a v3 transaction with `l2_gas.max_price_per_unit = 1 fri`.
8. The blockifier rejects it with `MaxGasPriceTooLow` because the actual block L2 gas price is ~1 Gwei.

Relevant code path:

| Step | File | Lines |
|------|------|-------|
| Stale fallback construction | `crates/apollo_rpc/src/v0_8/api/api_impl.rs` | 1573–1594 |
| Zero → MIN substitution | `crates/apollo_rpc_execution/src/lib.rs` | 386–396 |
| Wrong price in response | `crates/apollo_rpc_execution/src/objects.rs` | 165–182 |
| Execution rejection | `crates/blockifier/src/transaction/account_transaction.rs` | 437–445 |

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1553-1595)
```rust
async fn read_pending_data<Mode: TransactionKind>(
    pending_data: &Arc<RwLock<PendingData>>,
    txn: &StorageTxn<'_, Mode>,
) -> RpcResult<PendingData> {
    let latest_header = match get_latest_block_number(txn)? {
        Some(latest_block_number) => get_block_header_by_number(txn, latest_block_number)?,
        None => starknet_api::block::BlockHeader {
            // TODO(Shahak): Consider adding genesis hash to the config to support chains that have
            // different genesis hash.
            block_header_without_hash: BlockHeaderWithoutHash {
                parent_hash: BlockHash::GENESIS_PARENT_HASH,
                ..Default::default()
            },
            ..Default::default()
        },
    };
    let pending_data = &pending_data.read().await;
    if pending_data.block.parent_block_hash() == latest_header.block_hash {
        Ok((*pending_data).clone())
    } else {
        Ok(PendingData {
            block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
                parent_block_hash: latest_header.block_hash,
                eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
                strk_l1_gas_price: latest_header
                    .block_header_without_hash
                    .l1_gas_price
                    .price_in_fri,
                timestamp: latest_header.block_header_without_hash.timestamp,
                sequencer_address: latest_header.block_header_without_hash.sequencer,
                starknet_version: latest_header
                    .block_header_without_hash
                    .starknet_version
                    .to_string(),
                ..Default::default()
            }),
            state_update: ClientPendingStateUpdate {
                old_root: latest_header.block_header_without_hash.state_root,
                state_diff: Default::default(),
            },
        })
    }
}
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L162-168)
```rust
    pub fn l2_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, L2 gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
        }
    }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L386-396)
```rust
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
            strk_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L165-182)
```rust
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
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L437-445)
```rust
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
```
