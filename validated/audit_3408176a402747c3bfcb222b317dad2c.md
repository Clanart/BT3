### Title
Fee Escalation Threshold Rounds to Zero for Small Tip/Gas Values, Bypassing Replacement Guard - (File: crates/apollo_mempool/src/mempool.rs)

### Summary

The `increased_enough` function in the mempool computes the required fee escalation increase using integer division `(existing_value * percentage) / 100`. When `existing_value` is smaller than `100 / percentage`, the integer division truncates the increase to zero, collapsing the replacement threshold to exactly `existing_value`. Any incoming transaction with a tip or max-L2-gas-price equal to the existing transaction's value then passes the escalation guard, even though the fee did not increase at all.

### Finding Description

`increased_enough` in `crates/apollo_mempool/src/mempool.rs` is the sole arithmetic gate that enforces fee escalation before a transaction is allowed to replace an existing one in the pool:

```rust
fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
    let percentage = u128::from(self.config.static_config.fee_escalation_percentage);
    let Some(escalation_qualified_value) = existing_value
        .checked_mul(percentage)
        .map(|v| v / 100)           // ← integer division truncates to 0
        .and_then(|increase| existing_value.checked_add(increase))
    else { return false; };
    incoming_value >= escalation_qualified_value
}
```

The production default is `fee_escalation_percentage = 10` (confirmed in both `MempoolStaticConfig::default()` and the deployed `mempool_config.json`). For this setting the increase term `existing_value * 10 / 100` is zero for every `existing_value` in `[0, 9]`. The threshold therefore becomes `existing_value + 0 = existing_value`, so the strict-increase requirement silently degrades to a non-strict `>=` comparison. A replacement transaction carrying the identical tip and max-L2-gas-price values passes `should_replace_tx` and is admitted by `handle_fee_escalation`, which then evicts the existing transaction and inserts the new one.

The same truncation applies to `max_l2_gas_price` independently. Both fields must pass `increased_enough`; if either field's existing value is below the truncation threshold, that field's escalation requirement is nullified.

The structural parallel to the external report is exact: the external Governor divides `totalSupply * bps` by `10_000` and the result collapses to zero for small supplies; here the mempool divides `existing_value * percentage` by `100` and the result collapses to zero for small fee values.

### Impact Explanation

`handle_fee_escalation` is called on every `add_transaction` and `validate_transaction` path. When the guard is bypassed, the mempool **accepts a replacement transaction that should have been rejected** with `MempoolError::DuplicateNonce`. Concretely:

- An attacker submits a transaction with `tip = 1` and `max_l2_gas_price = 1` (both below the truncation threshold of 10 for the default 10 % config).
- The attacker immediately resubmits a transaction for the same `(address, nonce)` with identical or only marginally different fee fields.
- `increased_enough(1, 1)` → `increase = 1*10/100 = 0` → `threshold = 1` → `1 >= 1` → `true`. The replacement is accepted.
- The attacker can repeat this indefinitely, cycling the pool slot without ever paying a higher fee, defeating the anti-spam and anti-front-running purpose of fee escalation entirely for low-fee transactions.

This matches the allowed impact: **"High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

### Likelihood Explanation

- The default and deployed `fee_escalation_percentage` is 10, making the truncation threshold 9 (any `existing_value ≤ 9` is affected).
- No privileged access is required; any unprivileged user can submit transactions with small fee values.
- The existing test suite (`test_fee_escalation_valid_replacement`, `test_fee_escalation_invalid_replacement`) only exercises values ≥ 90, so the truncation regime is entirely untested and the bug is undetected by the current test coverage.
- The `test_fee_escalation_valid_replacement_minimum_values` test uses `fee_escalation_percentage = 0` (always-replace mode), which masks the issue.

### Recommendation

Replace floor division with ceiling division for the increase term so that any non-zero `(existing_value, percentage)` pair always produces an increase of at least 1:

```rust
// Ceiling division: (existing_value * percentage + 99) / 100
let increase = existing_value
    .checked_mul(percentage)
    .and_then(|v| v.checked_add(99))
    .map(|v| v / 100);
```

Alternatively, enforce a minimum increase of 1 whenever `percentage > 0`:

```rust
let raw_increase = (existing_value * percentage) / 100;
let increase = if percentage > 0 { raw_increase.max(1) } else { 0 };
```

Either fix ensures that the escalation guard always requires a strictly higher fee value, regardless of how small the existing fee is.

### Proof of Concept

With the production default `fee_escalation_percentage = 10`:

| `existing_value` | `existing_value * 10` | `/ 100` (floor) | `threshold` | `incoming = existing` passes? |
|---|---|---|---|---|
| 1 | 10 | **0** | 1 | **yes (should be no)** |
| 5 | 50 | **0** | 5 | **yes (should be no)** |
| 9 | 90 | **0** | 9 | **yes (should be no)** |
| 10 | 100 | 1 | 11 | no (correct) |

Step-by-step exploit:
1. Submit `tx_A` with `address = A`, `nonce = N`, `tip = 5`, `max_l2_gas_price = 5`. Accepted into pool.
2. Submit `tx_B` with `address = A`, `nonce = N`, `tip = 5`, `max_l2_gas_price = 5`, different calldata. `increased_enough(5, 5)` → `5*10/100 = 0` → `threshold = 5` → `5 >= 5` → `true`. `tx_A` is evicted; `tx_B` is inserted.
3. Repeat step 2 indefinitely with zero fee increase, cycling the pool slot at will. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L724-754)
```rust
    fn handle_fee_escalation(
        &mut self,
        incoming_tx_reference: TransactionReference,
        validation_only: bool,
    ) -> MempoolResult<()> {
        let TransactionReference { address, nonce, .. } = incoming_tx_reference;

        self.validate_no_delayed_declare_front_run(incoming_tx_reference)?;

        if !self.config.static_config.enable_fee_escalation {
            if self.tx_pool.get_by_address_and_nonce(address, nonce).is_some() {
                return Err(MempoolError::DuplicateNonce { address, nonce });
            };

            return Ok(());
        }

        let Some(existing_tx_reference) = self.tx_pool.get_by_address_and_nonce(address, nonce)
        else {
            // Replacement irrelevant: no existing transaction with the same nonce for address.
            return Ok(());
        };

        if !self.should_replace_tx(&existing_tx_reference, &incoming_tx_reference) {
            info!(
                "{existing_tx_reference} was not replaced by {incoming_tx_reference} due to \
                 insufficient fee escalation."
            );
            // TODO(Elin): consider adding a more specific error type / message.
            return Err(MempoolError::DuplicateNonce { address, nonce });
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L784-799)
```rust
    fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
        let percentage = u128::from(self.config.static_config.fee_escalation_percentage);

        // Note: To reduce precision loss, we first multiply by the percentage and then divide by
        // 100. This could cause an overflow and an automatic rejection of the transaction, but the
        // values aren't expected to be large enough for this to be an issue.
        let Some(escalation_qualified_value) = existing_value
            .checked_mul(percentage)
            .map(|v| v / 100)
            .and_then(|increase| existing_value.checked_add(increase))
        else {
            // Overflow occurred during calculation; reject the transaction.
            return false;
        };

        incoming_value >= escalation_qualified_value
```

**File:** crates/apollo_mempool_config/src/config.rs (L62-86)
```rust
    pub fee_escalation_percentage: u8, // E.g., 10 for a 10% increase.
    // If true, only transactions with max L2 gas price per unit bound that are above the threshold
    // are inserted into the priority queue. If false, all transactions are inserted into the
    // priority queue.
    pub validate_resource_bounds: bool,
    // Time to wait before allowing a Declare transaction to be returned in `get_txs`.
    // Declare transactions are delayed to allow other nodes sufficient time to compile them.
    #[serde(deserialize_with = "deserialize_seconds_to_duration")]
    pub declare_delay: Duration,
    // Number of latest committed blocks for which committed account nonces are preserved.
    pub committed_nonce_retention_block_count: usize,
    // The maximum size of the mempool, in bytes.
    pub capacity_in_bytes: u64,
    // Determines queue type and other behavior.
    pub behavior_mode: BehaviorMode,
    // The URL of the recorder service (used for FIFO queue timestamp fetching).
    pub recorder_url: Url,
}

impl Default for MempoolStaticConfig {
    fn default() -> Self {
        Self {
            enable_fee_escalation: true,
            validate_resource_bounds: true,
            fee_escalation_percentage: 10,
```

**File:** crates/apollo_deployments/resources/app_configs/mempool_config.json (L1-8)
```json
{
  "mempool_config.dynamic_config.transaction_ttl": 300,
  "mempool_config.static_config.capacity_in_bytes": 1073741824,
  "mempool_config.static_config.committed_nonce_retention_block_count": 100,
  "mempool_config.static_config.declare_delay": 20,
  "mempool_config.static_config.enable_fee_escalation": true,
  "mempool_config.static_config.fee_escalation_percentage": 10
}
```
