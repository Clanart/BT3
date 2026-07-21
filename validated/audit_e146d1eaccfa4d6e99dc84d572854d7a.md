### Title
Stale Pending-Block Fallback Silently Zeroes `l1_data_gas_price` and `l2_gas_price`, Causing Wrong Fee Estimates for Pending-Block Queries — (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory pending block is stale (its `parent_block_hash` does not match the latest committed block hash), `read_pending_data` synthesises a `DeprecatedPendingBlock` fallback. That type carries no `l1_data_gas_price` or `l2_gas_price` fields; both accessors return `GasPricePerToken::default()` (zero). The zeros propagate through `client_pending_data_to_execution_pending_data` into `prepare_block_context`, where `NonzeroGasPrice::new(0).unwrap_or(NonzeroGasPrice::MIN)` silently substitutes the minimum value (1) for both prices. Any call to `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_call` with `BlockId::Tag(Tag::Pending)` during this window executes against a block context whose data-gas and L2-gas prices are orders of magnitude below reality, producing authoritative-looking but materially wrong fee estimates.

---

### Finding Description

`read_pending_data` checks whether the cached pending block's `parent_block_hash` equals the latest committed block's hash. When they differ — a normal transient condition that occurs every time a new block is committed before the pending-data cache is refreshed — it constructs a synthetic fallback: