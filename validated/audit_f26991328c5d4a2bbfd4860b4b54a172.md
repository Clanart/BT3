The code path is fully traceable. Let me confirm the exact lines in the execution layer.

### Title
Stale-Pending Fallback in `read_pending_data` Hardcodes `l1_da_mode = Calldata`, Corrupting `use_kzg_da` in `BlockContext` for Fee Estimation on BLOB-DA Chains — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory pending data is stale (its `parent_block_hash` does not match the latest accepted block hash), `read_pending_data` synthesises a fallback `DeprecatedPendingBlock`. That struct has no `l1_da_mode` field; the `PendingBlockOrDeprecated::l1_da_mode()` dispatch arm for the `Deprecated` variant unconditionally returns `L1DataAvailabilityMode::Calldata`. On a chain whose latest accepted block carries `l1_da_mode = BLOB`, this causes every call to `estimate_fee` or `simulate_transactions` with `Tag::Pending` that lands during the stale window to build a `BlockContext` with `use_kzg_da = false`, producing wrong data-gas costs.

---

### Finding Description

**Step 1 — Fallback construction omits `l1_da_mode`.**

`read_pending_data` copies several fields from the latest accepted block header into the synthetic block, but `l1_da_mode` is not among them: [1](#0-0) 

The `DeprecatedPendingBlock` struct has no `l1_da_mode` field at all: [2](#0-1) 

**Step 2 — `l1_da_mode()` on the `Deprecated` variant is hardcoded to `Calldata`.** [3](#0-2) 

The comment "In older versions, all blocks were using calldata" explains the intent for historical data, but the same arm is hit for the synthetic fallback block regardless of the chain's current DA mode.

**Step 3 — `estimate_fee` reads `l1_da_mode` from the fallback block.** [4](#0-3) 

**Step 4 — `BlockContext` construction converts `l1_da_mode` directly to `use_kzg_da`.** [5](#0-4) 

When `maybe_pending_data` is `Some`, `l1_da_mode` comes from the fallback block → `Calldata` → `use_kzg_da = false`.

**Step 5 — Fee computation uses `use_kzg_da` for DA gas.** [6](#0-5) 

With `use_kzg_da = false`, `da_gas_vector` routes state-diff bytes through L1 calldata pricing instead of blob pricing, and `to_gas_vector` omits the blob DA component entirely.

---

### Impact Explanation

Any unprivileged caller who issues `starknet_estimateFee` or `starknet_simulateTransactions` with `block_id = Tag::Pending` during the stale window receives a fee estimate computed with `use_kzg_da = false`. On a BLOB-DA chain this means:

- `l1_data_gas_consumed` in the response is zero or wrong (calldata path charges L1 gas, not data gas).
- A v3 transaction that sets `l1_data_gas` resource bounds based on this estimate will be under-bounded and may be rejected by the sequencer, or over-bounded and overpay.
- The returned value is authoritative-looking (no error, no warning), matching the "High — RPC fee estimation returns an authoritative-looking wrong value" impact category.

---

### Likelihood Explanation

The stale window is a normal, recurring condition: it opens every time a new block is accepted and closes when the pending-data feed delivers an update with the new parent hash. On a chain with short block times this window is brief but guaranteed to occur. Any caller polling `estimate_fee` at `Tag::Pending` will hit it with non-negligible frequency. No special privilege or timing attack is required; the caller simply issues a standard RPC call.

---

### Recommendation

In the fallback branch of `read_pending_data`, copy `l1_da_mode` from the latest accepted block header and use `PendingBlockOrDeprecated::Current(PendingBlock { … l1_da_mode: latest_header.block_header_without_hash.l1_da_mode, … })` instead of `Deprecated`. Alternatively, add an `l1_da_mode` field to `DeprecatedPendingBlock` and populate it from the header in the fallback. Either change ensures the synthesised pending block reflects the chain's actual DA mode.

---

### Proof of Concept

```
Precondition: storage contains block N with l1_da_mode = BLOB.
Pending data in memory has parent_block_hash = block N-1 hash (stale).

1. RPC caller sends starknet_estimateFee({
       transactions: [v3_invoke_tx],
       block_id: Tag::Pending
   })

2. read_pending_data:
   latest_header.block_hash = hash(N)          // BLOB chain
   pending_data.parent_block_hash = hash(N-1)  // mismatch → fallback
   → returns DeprecatedPendingBlock { … }

3. l1_da_mode() on Deprecated → Calldata

4. BlockContext: use_kzg_da = Calldata.is_use_kzg_da() = false

5. da_gas_vector(false) → routes through L1 calldata gas, not blob gas
   → l1_data_gas_consumed = 0 in response

6. Correct estimate (use_kzg_da = true) would return
   l1_data_gas_consumed > 0 (blob DA cost for state diff bytes)

Assert: response.l1_data_gas_consumed == 0
        correct_estimate.l1_data_gas_consumed > 0   // invariant violated
```

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1572-1594)
```rust
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

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L169-175)
```rust
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

**File:** crates/apollo_rpc/src/pending.rs (L17-22)
```rust
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L340-369)
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
```

**File:** crates/blockifier/src/fee/receipt.rs (L105-124)
```rust
        let gas = tx_resources.to_gas_vector(
            &tx_context.block_context.versioned_constants,
            tx_context.block_context.block_info.use_kzg_da,
            &gas_mode,
        );
        // Backward-compatibility.
        let fee = if tx_type == TransactionType::Declare && tx_context.tx_info.is_v0() {
            Fee(0)
        } else {
            tx_context.tx_info.get_fee_by_gas_vector(
                &tx_context.block_context.block_info,
                gas,
                tx_context.effective_tip(),
            )
        };

        let da_gas = tx_resources
            .starknet_resources
            .state
            .da_gas_vector(tx_context.block_context.block_info.use_kzg_da);
```
