### Title
Floor Division in `increased_enough` Allows Zero-Increase Fee Escalation, Bypassing Mempool Replacement Guard - (File: `crates/apollo_mempool/src/mempool.rs`)

### Summary

The `increased_enough` function in the mempool uses integer floor division (`v / 100`) to compute the required fee escalation increase. When `existing_value * fee_escalation_percentage < 100`, the computed increase truncates to zero, making the replacement threshold equal to the existing value itself. A transaction with identical tip and `max_l2_gas_price` is then accepted as a valid replacement, bypassing the fee escalation invariant entirely.

### Finding Description

In `crates/apollo_mempool/src/mempool.rs`, `increased_enough` computes the escalation threshold as:

```rust
fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
    let percentage = u128::from(self.config.static_config.fee_escalation_percentage);
    let Some(escalation_qualified_value) = existing_value
        .checked_mul(percentage)
        .map(|v| v / 100)          // ← floor division; rounds increase DOWN
        .and_then(|increase| existing_value.checked_add(increase))
    else { return false; };
    incoming_value >= escalation_qualified_value
}
``` [1](#0-0) 

The division `v / 100` is Rust integer floor division. When `existing_value * percentage < 100`, the quotient is `0`, so `escalation_qualified_value = existing_value + 0 = existing_value`. The guard `incoming_value >= escalation_qualified_value` then passes for any incoming value that is **equal to** the existing value, meaning no actual fee increase is required.

`should_replace_tx` calls `increased_enough` for both `tip` and `max_l2_gas_price`: [2](#0-1) 

The production default `fee_escalation_percentage` is `10` (confirmed in both the config default and the deployment config): [3](#0-2) [4](#0-3) 

With `percentage = 10`, any `existing_value < 10` triggers the zero-increase path:
```
existing_value = 9, percentage = 10
9 * 10 = 90
90 / 100 = 0   (floor)
escalation_qualified_value = 9 + 0 = 9
incoming_value = 9  →  9 >= 9  →  true  (replacement accepted)
```

`handle_fee_escalation` then removes the existing transaction and accepts the replacement: [5](#0-4) 

### Impact Explanation

The mempool's fee escalation guard is the sole mechanism preventing a sender from repeatedly replacing their own pending transaction without paying a higher fee. When the guard is bypassed, the mempool accepts a replacement transaction that should be rejected (`DuplicateNonce` error). This matches the allowed impact: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

Concretely:
- A sender with a pending transaction whose `tip` or `max_l2_gas_price` is below the rounding threshold (< 10 with the default 10% setting) can submit an identical replacement indefinitely, evicting the original without any fee increase.
- Because `handle_fee_escalation` is also called from the `validate_tx` path (the `validation_only = true` branch), the RPC fee-estimation and validation endpoints also return an authoritative "valid" result for what is actually a non-escalating replacement.

### Likelihood Explanation

The production `fee_escalation_percentage` is `10`. The threshold below which rounding collapses to zero is `existing_value < 10`. Tip values are denominated in fri (the smallest STRK unit) and `max_l2_gas_price` is a raw `u128` price per gas unit. Low-fee or zero-tip transactions (e.g., transactions submitted during low-congestion periods or by accounts that set minimal tips) can easily fall below this threshold. The existing test suite uses `tip: 100` and `max_l2_gas_price: 100` as the baseline for escalation tests, which are above the threshold and therefore do not exercise the rounding-to-zero case. [6](#0-5) 

### Recommendation

Replace the floor division with ceiling division for the escalation increase, so that any non-zero product rounds up to at least 1:

```rust
// Replace:
.map(|v| v / 100)
// With:
.map(|v| v.div_ceil(100))   // available in Rust 1.73+ on integer types
```

This ensures that whenever `existing_value > 0` and `percentage > 0`, the required increase is at least 1, preserving the invariant that a replacement transaction must strictly exceed the existing one.

### Proof of Concept

```rust
// Reproduces the zero-increase bypass with the production default of 10%.
let existing_tip: u128 = 9;
let percentage: u128 = 10; // fee_escalation_percentage = 10

let increase = (existing_tip * percentage) / 100; // = 90 / 100 = 0
let threshold = existing_tip + increase;           // = 9 + 0 = 9

// A replacement with the same tip passes:
let incoming_tip: u128 = 9;
assert!(incoming_tip >= threshold); // true — replacement accepted without fee increase
```

With `fee_escalation_percentage = 10`, any existing `tip` or `max_l2_gas_price` in the range `[1, 9]` produces a zero required increase, allowing same-fee replacement. The same applies to `max_l2_gas_price` independently, so both fields must be in the safe range for the guard to function correctly.

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L724-768)
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

        if validation_only {
            return Ok(());
        }

        debug!("{existing_tx_reference} will be replaced by {incoming_tx_reference}.");

        self.tx_queue.remove_txs(&[existing_tx_reference]);
        self.tx_pool
            .remove(existing_tx_reference.tx_hash)
            .expect("Transaction hash from pool must exist.");

        Ok(())
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L770-782)
```rust
    fn should_replace_tx(
        &self,
        existing_tx: &TransactionReference,
        incoming_tx: &TransactionReference,
    ) -> bool {
        let [existing_tip, incoming_tip] =
            [existing_tx, incoming_tx].map(|tx| u128::from(tx.tip.0));
        let [existing_max_l2_gas_price, incoming_max_l2_gas_price] =
            [existing_tx, incoming_tx].map(|tx| tx.max_l2_gas_price.0);

        self.increased_enough(existing_tip, incoming_tip)
            && self.increased_enough(existing_max_l2_gas_price, incoming_max_l2_gas_price)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L784-800)
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
    }
```

**File:** crates/apollo_mempool_config/src/config.rs (L81-96)
```rust
impl Default for MempoolStaticConfig {
    fn default() -> Self {
        Self {
            enable_fee_escalation: true,
            validate_resource_bounds: true,
            fee_escalation_percentage: 10,
            declare_delay: Duration::from_secs(1),
            committed_nonce_retention_block_count: 100,
            capacity_in_bytes: 1 << 30, // 1GB.
            behavior_mode: BehaviorMode::Starknet,
            recorder_url: "https://recorder_url"
                .parse::<Url>()
                .expect("recorder_url must be a valid Recorder URL"),
        }
    }
}
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

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L782-809)
```rust
    let existing_tx = tx!(tx_hash: 1, tip: 100, max_l2_gas_price: 100);

    let mut builder = builder_with_queue(in_priority_queue, in_pending_queue, &existing_tx)
        .with_fee_escalation_percentage(10);

    if in_pending_queue {
        // An arbitrary threashold such that the added transaction would have entered the
        // appropriate queue.
        let gas_price_threshold = if escalate_to_priority { 101 } else { 1000 };
        builder = builder.with_gas_price_threshold(gas_price_threshold);
    }

    let mempool = builder.with_pool([existing_tx.clone()]).build_full_mempool();

    let input_not_enough_tip = add_tx_input!(tx_hash: 3, tip: 109, max_l2_gas_price: 110);
    let input_not_enough_gas_price = add_tx_input!(tx_hash: 4, tip: 110, max_l2_gas_price: 109);
    let input_not_enough_both = add_tx_input!(tx_hash: 5, tip: 109, max_l2_gas_price: 109);

    // Test and assert.
    let invalid_replacement_inputs =
        [input_not_enough_tip, input_not_enough_gas_price, input_not_enough_both];
    validate_and_add_txs_and_verify_no_replacement(
        mempool,
        existing_tx,
        invalid_replacement_inputs,
        in_priority_queue,
        in_pending_queue,
    );
```
