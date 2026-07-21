### Title
Stale-Pending Fallback in `read_pending_data` Omits `l1_data_gas_price`, `l2_gas_price`, and `l1_da_mode`, Causing Wrong Fee Estimates for `BlockId::Tag(Tag::Pending)` - (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the cached pending block's parent hash does not match the latest committed block hash, `read_pending_data` constructs a synthetic fallback using `DeprecatedPendingBlock`. This type structurally cannot carry `l1_data_gas_price`, `l2_gas_price`, or `l1_da_mode`; those fields return zero/`Calldata` by design for the deprecated variant. The fallback is then consumed by `estimate_fee`, `simulate_transactions`, and `estimate_message_fee` via `client_pending_data_to_execution_pending_data`, which faithfully reads these zero values into `ExecutionPendingData`. The block-context builder clamps zero gas prices to `NonzeroGasPrice::MIN` (1 wei) and forces `use_kzg_da = false`. The result is an authoritative-looking but severely wrong fee estimate returned to callers.

---

### Finding Description

**Root cause — `read_pending_data` fallback uses the wrong block type:** [1](#0-0) 

When `pending_data.block.parent_block_hash() != latest_header.block_hash` (stale), the function constructs a `DeprecatedPendingBlock` and copies only `l1_gas_price` (as `eth_l1_gas_price`/`strk_l1_gas_price`), `timestamp`, `sequencer_address`, and `starknet_version` from the latest header. It does **not** copy `l1_data_gas_price` or `l2_gas_price`.

**`DeprecatedPendingBlock` has no `l1_data_gas_price` or `l2_gas_price` fields:** [2](#0-1) 

The accessor methods for the `Deprecated` variant return zero for both missing prices and hardcode `Calldata` for DA mode: [3](#0-2) 

**Conversion to `ExecutionPendingData` faithfully reads the zero values:** [4](#0-3) 

Lines 19–21 call `l1_data_gas_price()`, `l2_gas_price()`, and `l1_da_mode()` on the fallback block, receiving `GasPricePerToken::default()` (zero) and `L1DataAvailabilityMode::Calldata`.

**Block-context builder clamps zeros to `NonzeroGasPrice::MIN` and forces `use_kzg_da = false`:** [5](#0-4) [6](#0-5) 

`NonzeroGasPrice::new(0)` returns `None`, so both `l1_data_gas_price` and `l2_gas_price` are clamped to `NonzeroGasPrice::MIN` (1 wei). `l1_da_mode.is_use_kzg_da()` on `Calldata` returns `false`, so `use_kzg_da = false` regardless of the actual network DA mode.

**All three execution RPC endpoints are affected:** [7](#0-6) [8](#0-7) [9](#0-8) 

`estimate_fee`, `simulate_transactions`, and `estimate_message_fee` all call `read_pending_data` then `client_pending_data_to_execution_pending_data` when `block_id = Tag::Pending`.

---

### Impact Explanation

Any call to `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_estimateMessageFee` with `block_id = "pending"` during the stale window returns a fee estimate computed with:

- `l1_data_gas_price` = 1 wei (instead of the actual value, e.g., thousands of wei on mainnet) → DA costs severely underestimated
- `l2_gas_price` = 1 wei (instead of actual) → L2 execution costs severely underestimated
- `use_kzg_da` = `false` (instead of actual KZG mode) → wrong DA cost formula applied

Wallets and dApps that use these estimates to set `resource_bounds` will submit transactions with insufficient fees, causing them to fail at execution. The response is indistinguishable from a correct estimate — it carries no staleness indicator — matching the "authoritative-looking wrong value" impact criterion.

---

### Likelihood Explanation

The pending data becomes stale on every block transition: the moment a new block is committed to storage, `latest_header.block_hash` advances while the cached `pending_data.block.parent_block_hash()` still points to the previous block. This window persists until the pending-sync loop fetches fresh data from the feeder gateway. During normal operation this window is short but recurs with every block (~30 seconds on mainnet). Any client polling `estimate_fee` with `"pending"` during this window receives wrong values. No privileged access or adversarial action is required.

---

### Recommendation

Replace `DeprecatedPendingBlock` with `PendingBlock` in the fallback construction and copy all three gas prices and the DA mode from the latest committed header:

```rust
// In read_pending_data, stale branch:
block: PendingBlockOrDeprecated::Current(PendingBlock {
    parent_block_hash: latest_header.block_hash,
    l1_gas_price: latest_header.block_header_without_hash.l1_gas_price,
    l1_data_gas_price: latest_header.block_header_without_hash.l1_data_gas_price,
    l2_gas_price: latest_header.block_header_without_hash.l2_gas_price,
    l1_da_mode: latest_header.block_header_without_hash.l1_da_mode,
    timestamp: latest_header.block_header_without_hash.timestamp,
    sequencer_address: latest_header.block_header_without_hash.sequencer,
    starknet_version: latest_header.block_header_without_hash.starknet_version.to_string(),
    ..Default::default()
}),
```

This mirrors the pattern already used for `l1_gas_price` and ensures all three gas prices and the DA mode are consistent with the latest committed state.

---

### Proof of Concept

1. Node has committed block N with `l1_data_gas_price = 1000 wei`, `l2_gas_price = 500 wei`, `l1_da_mode = KZG`.
2. Pending sync has not yet fetched a fresh pending block; `pending_data.block.parent_block_hash()` still points to block N-1.
3. Client calls `starknet_V0_8_estimateFee` with `block_id = "pending"` for a transaction that consumes significant L2 gas and DA.
4. `read_pending_data` detects the mismatch and returns a fallback `DeprecatedPendingBlock` with `eth_l1_gas_price = latest.l1_gas_price.price_in_wei` but `l1_data_gas_price = 0`, `l2_gas_price = 0`.
5. `client_pending_data_to_execution_pending_data` reads `l1_data_gas_price() = GasPricePerToken::default()` and `l2_gas_price() = GasPricePerToken::default()`.
6. `create_block_context` clamps both to `NonzeroGasPrice::MIN` (1 wei) and sets `use_kzg_da = false`.
7. Fee estimation returns `overall_fee` computed with 1 wei data gas and 1 wei L2 gas — orders of magnitude below the true cost.
8. Client sets `resource_bounds` from this estimate; transaction is submitted and fails with `InsufficientResourceBounds` at sequencer validation.

The analog to M-16 is exact: just as `maxWithdraw` returns `balanceOf[user]` when it should return 0 because the withdrawal window is closed, `estimate_fee` returns a non-zero fee estimate computed from structurally-zeroed gas prices when the pending state is unavailable — instead of either returning an error or using the correct prices from the latest committed header.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1079-1086)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1437-1444)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1569-1594)
```rust
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
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-175)
```rust
    pub fn l1_data_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, data gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l1_data_gas_price,
        }
    }
    pub fn l2_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, L2 gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
        }
    }
    pub fn l1_da_mode(&self) -> L1DataAvailabilityMode {
        match self {
            // In older versions, all blocks were using calldata.
            PendingBlockOrDeprecated::Deprecated(_) => L1DataAvailabilityMode::Calldata,
            PendingBlockOrDeprecated::Current(block) => block.l1_da_mode,
        }
    }
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L178-195)
```rust
#[derive(Debug, Default, Deserialize, Clone, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DeprecatedPendingBlock {
    #[serde(flatten)]
    pub accepted_on_l2_extra_data: Option<AcceptedOnL2ExtraData>,
    pub parent_block_hash: BlockHash,
    pub status: BlockStatus,
    // In older versions, eth_l1_gas_price was named gas_price and there was no strk_l1_gas_price.
    #[serde(alias = "gas_price")]
    pub eth_l1_gas_price: GasPrice,
    #[serde(default)]
    pub strk_l1_gas_price: GasPrice,
    pub transactions: Vec<Transaction>,
    pub timestamp: BlockTimestamp,
    pub sequencer_address: SequencerContractAddress,
    pub transaction_receipts: Vec<TransactionReceipt>,
    pub starknet_version: String,
}
```

**File:** crates/apollo_rpc/src/pending.rs (L5-23)
```rust
pub(crate) fn client_pending_data_to_execution_pending_data(
    client_pending_data: ClientPendingData,
    pending_classes: PendingClasses,
) -> ExecutionPendingData {
    ExecutionPendingData {
        storage_diffs: client_pending_data.state_update.state_diff.storage_diffs,
        deployed_contracts: client_pending_data.state_update.state_diff.deployed_contracts,
        declared_classes: client_pending_data.state_update.state_diff.declared_classes,
        old_declared_contracts: client_pending_data.state_update.state_diff.old_declared_contracts,
        nonces: client_pending_data.state_update.state_diff.nonces,
        replaced_classes: client_pending_data.state_update.state_diff.replaced_classes,
        classes: pending_classes,
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
    }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L340-397)
```rust
    ) = match maybe_pending_data {
        Some(pending_data) => (
            block_context_number.unchecked_next(),
            pending_data.timestamp,
            pending_data.l1_gas_price,
            pending_data.l1_data_gas_price,
            pending_data.l2_gas_price,
            pending_data.sequencer,
            pending_data.l1_da_mode,
        ),
        None => {
            let header = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_context_number)?
                .expect("Should have block header.")
                .block_header_without_hash;
            (
                header.block_number,
                header.timestamp,
                header.l1_gas_price,
                header.l1_data_gas_price,
                header.l2_gas_price,
                header.sequencer,
                header.l1_da_mode,
            )
        }
    };
    let ten_blocks_ago = get_10_blocks_ago(&block_context_number, cached_state)?;

    let use_kzg_da = if override_kzg_da_to_false { false } else { l1_da_mode.is_use_kzg_da() };
    let starknet_version = storage_reader
        .begin_ro_txn()?
        .get_starknet_version(block_number)?
        .unwrap_or(StarknetVersion::LATEST);
    let block_info = BlockInfo {
        block_timestamp,
        sequencer_address: sequencer_address.0,
        use_kzg_da,
        block_number,
        // TODO(yair): What to do about blocks pre 0.13.1 where the data gas price were 0?
        gas_prices: GasPrices {
            eth_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
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
        },
```
