### Title
Fee Escalation Bypass via Integer Division Truncation in `increased_enough` - (File: `crates/apollo_mempool/src/mempool.rs`)

### Summary
The `increased_enough` function in the mempool computes the required fee escalation threshold using integer division that truncates to zero when the existing tip or `max_l2_gas_price` is small. This allows a replacement transaction to pass the fee escalation check without any actual increase, causing the mempool to accept an invalid replacement that should be rejected.

### Finding Description

The `increased_enough` function in `crates/apollo_mempool/src/mempool.rs` computes the minimum required value for a replacement transaction:

```rust
fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
    let percentage = u128::from(self.config.static_config.fee_escalation_percentage);

    let Some(escalation_qualified_value) = existing_value
        .checked_mul(percentage)
        .map(|v| v / 100)                                          // <-- truncating division
        .and_then(|increase| existing_value.checked_add(increase))
    else {
        return false;
    };

    incoming_value >= escalation_qualified_value
}
``` [1](#0-0) 

The `increase` is computed as `(existing_value * percentage) / 100`. When `existing_value * percentage < 100`, integer division truncates to `0`, making `escalation_qualified_value = existing_value + 0 = existing_value`. The check then becomes `incoming_value >= existing_value`, which passes even when the incoming transaction has the **exact same** tip and `max_l2_gas_price` as the existing one.

With the production default of `fee_escalation_percentage = 10`: [2](#0-1) 

Any `existing_value <= 9` triggers the truncation:
```
existing_value = 9, percentage = 10:
  increase = (9 * 10) / 100 = 90 / 100 = 0
  escalation_qualified_value = 9 + 0 = 9
  incoming_value = 9 → 9 >= 9 → PASSES (should require >= 10)
```

This is called from `should_replace_tx`, which gates `handle_fee_escalation`: [3](#0-2) 

And `handle_fee_escalation` is invoked on every `add_tx` and `validate_tx` path: [4](#0-3) 

### Impact Explanation

The mempool accepts a replacement transaction that does not satisfy the configured fee escalation requirement. Concretely:

- A user with a pending transaction at `tip = 9, max_l2_gas_price = 9` can submit a replacement with identical values and have it accepted, evicting the original.
- This bypasses the economic anti-spam mechanism of fee escalation entirely for small-valued transactions.
- The gateway forwards the replacement to the mempool after stateless and stateful validation, so the invalid admission occurs at the mempool layer and is reflected in what the sequencer sequences.

This matches: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

### Likelihood Explanation

The `tip` and `max_l2_gas_price` fields are user-controlled `u64`/`u128` values with no enforced minimum above zero. Any user can craft a transaction with small values (e.g., `tip = 1`) to trigger the truncation. The condition is trivially reachable with no special privileges. The production `fee_escalation_percentage` of 10 means any `existing_value <= 9` is affected. [5](#0-4) 

### Recommendation

Replace the truncating division with ceiling division for the `increase` computation, ensuring that any non-zero percentage always produces a non-zero required increase:

```rust
// Instead of: existing_value.checked_mul(percentage).map(|v| v / 100)
// Use ceiling division:
existing_value.checked_mul(percentage).map(|v| (v + 99) / 100)
```

Alternatively, enforce a minimum `increase` of 1 whenever `percentage > 0` and `existing_value > 0`, mirroring the "minimum fee threshold" recommendation from the external report.

### Proof of Concept

1. Configure mempool with `fee_escalation_percentage = 10` (production default).
2. Submit transaction T1 with `tip = 9, max_l2_gas_price = 9` for address A, nonce N.
3. T1 is accepted and sits in the pool.
4. Submit replacement T2 with `tip = 9, max_l2_gas_price = 9` (identical values), same address A, nonce N.
5. `increased_enough(9, 9)` computes: `increase = (9 * 10) / 100 = 0`, `escalation_qualified_value = 9`, `9 >= 9` → `true`.
6. `should_replace_tx` returns `true`; `handle_fee_escalation` removes T1 and accepts T2.
7. The mempool has accepted a replacement that provided zero fee increase, violating the escalation invariant. [6](#0-5)

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L404-406)
```rust
    ) -> MempoolResult<()> {
        self.validate_incoming_tx(tx_reference, account_nonce)?;
        self.handle_fee_escalation(tx_reference, false)?;
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

**File:** crates/apollo_mempool_config/src/config.rs (L62-62)
```rust
    pub fee_escalation_percentage: u8, // E.g., 10 for a 10% increase.
```

**File:** crates/apollo_mempool_config/src/config.rs (L86-86)
```rust
            fee_escalation_percentage: 10,
```
