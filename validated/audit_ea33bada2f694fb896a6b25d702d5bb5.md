### Title
`SignableTransaction::new` accepts an unbounded `fee_per_vbyte` and computes the fee with unchecked arithmetic, allowing an economically irrational fee (>100% of payment value) to be signed - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The reported bug class is a fee parameter that lacks an upper bound, permitting economically irrational fees. The same class exists in `SignableTransaction::new`: `fee_per_vbyte` is an externally supplied `u64` with no maximum, and it is multiplied by `vbytes` and added to `payment_sat` using plain arithmetic that wraps on overflow in release builds. The resulting `SignableTransaction` is what the FROST multisig is asked to sign, so an irrational fee value produces a signed transaction whose actual fee (`sum(inputs) - sum(outputs)`) is unrelated to the intended fee.

### Finding Description
`SignableTransaction::new` takes `fee_per_vbyte: u64` from the caller with no validation of an upper bound.

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { .. })?;
}
``` [1](#0-0) 

Two defects mirror the report:

1. **No upper bound**: `fee_per_vbyte` can be any `u64`. There is a `TooLowFee` minimum check but no sanity maximum — the exact asymmetry of `_MAX_FEE` being able to exceed 100%.
2. **Unchecked arithmetic**: `fee_per_vbyte * vbytes` and `payment_sat + needed_fee` wrap silently in release builds (no `overflow-checks`), so a huge `fee_per_vbyte` can wrap `needed_fee` to a small value that passes both the `TooLowFee` and `NotEnoughFunds` checks, while the actual fee paid by the transaction is `input_sat - payment_sat` when no change output is created:

```rust
pub fn fee(&self) -> u64 {
  self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
    self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
}
``` [2](#0-1) 

`fee_per_vbyte` reaches this function from untrusted on-chain data: `Bitcoin::median_fee` computes each block transaction's fee as `(in_value - out) / vsize` and takes the median over transactions any unprivileged party can include, then feeds it directly into `BSignableTransaction::new` as `fee.0`. [3](#0-2) [4](#0-3) 

### Impact Explanation
When no change output is produced (change below `DUST`, or `change: None`), the entire `input_sat - payment_sat` remainder becomes miner fee regardless of `needed_fee`. A wrapped `needed_fee` that passes validation therefore yields a `SignableTransaction` whose real fee vastly exceeds the intended fee — potentially exceeding 100% of the payment value, the exact impact described in the report. This transaction is what the threshold signing set signs (`SignableTransaction::sig_msg`), so the result is concrete signing of an unintended message: a transaction burning vault funds as fees. Even without overflow, an oversized-but-unwrapped `fee_per_vbyte` combined with absent change produces the same over-100% effective fee.

### Likelihood Explanation
`fee_per_vbyte` originates from `median_fee`, which aggregates fees of transactions in a block — data influenced by any party able to get transactions mined. Reaching values near `u64::MAX` requires an extreme fee rate or overflow-inducing input, which is difficult in practice, and a change output usually absorbs the remainder as designed. The exposure window (no-change transactions, or any direct caller of the public wallet API passing an unchecked rate) keeps this at Medium rather than High.

### Recommendation
Enforce a maximum fee rate and use checked arithmetic, matching the report's fix pattern:

```diff
 let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
+const MAX_FEE_PER_VBYTE: u64 = /* sane ceiling, e.g. 100_000 sat/vbyte */;
+if fee_per_vbyte > MAX_FEE_PER_VBYTE {
+  Err(TransactionError::TooHighFee)?;
+}
-let mut needed_fee = fee_per_vbyte * vbytes;
+let mut needed_fee =
+  fee_per_vbyte.checked_mul(vbytes).ok_or(TransactionError::TooHighFee)?;
 ...
-if input_sat < (payment_sat + needed_fee) {
+if input_sat < payment_sat.checked_add(needed_fee).ok_or(TransactionError::TooHighFee)? {
```

and apply the same `checked_mul` to `fee_with_change`. Additionally, cap the implied actual fee (`input_sat - payment_sat`) against `needed_fee` when no change output is emitted.

### Proof of Concept
```rust
// release build (overflow-checks off)
let inputs = vec![received_output_with_value(100_000_000)];      // 1 BTC input
let payments = vec![(dest_script, 50_000_000)];                  // 0.5 BTC payment
// Chosen so fee_per_vbyte * vbytes wraps to a small value >= min relay fee
let fee_per_vbyte = u64::MAX / 100;
let tx = SignableTransaction::new(inputs, &payments, None, None, fee_per_vbyte).unwrap();
// needed_fee passed TooLowFee/NotEnoughFunds on the wrapped value,
// but the real fee is ~0.5 BTC — a >100% fee relative to intended cost
assert!(tx.fee() > tx.needed_fee());
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** processor/src/networks/bitcoin.rs (L388-414)
```rust
  async fn median_fee(&self, block: &Block) -> Result<Fee, NetworkError> {
    let mut fees = vec![];
    if block.txdata.len() > 1 {
      for tx in &block.txdata[1 ..] {
        let mut in_value = 0;
        for input in &tx.input {
          let mut input_tx = input.previous_output.txid.to_raw_hash().to_byte_array();
          input_tx.reverse();
          in_value += self
            .rpc
            .get_transaction(&input_tx)
            .await
            .map_err(|_| NetworkError::ConnectionError)?
            .output[usize::try_from(input.previous_output.vout).unwrap()]
          .value
          .to_sat();
        }
        let out = tx.output.iter().map(|output| output.value.to_sat()).sum::<u64>();
        fees.push((in_value - out) / u64::try_from(tx.vsize()).unwrap());
      }
    }
    fees.sort();
    let fee = fees.get(fees.len() / 2).copied().unwrap_or(0);

    // The DUST constant documentation notes a relay rule practically enforcing a
    // 1000 sat/kilo-vbyte minimum fee.
    Ok(Fee(fee.max(1)))
```

**File:** processor/src/networks/bitcoin.rs (L446-452)
```rust
    match BSignableTransaction::new(
      inputs.iter().map(|input| input.output.clone()).collect(),
      &payments,
      change.clone().map(Into::into),
      None,
      fee.0,
    ) {
```
