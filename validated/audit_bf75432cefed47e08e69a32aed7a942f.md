### Title
Minimum relay fee check truncates, permitting transactions below Bitcoin's required minimum fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

Analogous to a documented minimum bound being enforced too permissively, `SignableTransaction::new` intends to reject any transaction whose fee would not pass Bitcoin's default minimum relay fee, but the check uses truncating integer division, so the enforced minimum is up to ~1 sat/kvB-equivalent lower than the true policy minimum. A transaction can be constructed — and subsequently signed by the FROST multisig — that the Bitcoin network will not relay or mine.

### Finding Description

In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` validates the computed fee against `DEFAULT_MIN_RELAY_TX_FEE`:

```rust
// bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
``` [1](#0-0) 

`DEFAULT_MIN_RELAY_TX_FEE` is denominated in sats per kilo-vbyte (1000 sat/kvB = 1 sat/vB), so the true minimum fee for `vbytes` is `ceil(DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000)`. The code computes `floor(...)` instead. Whenever `(DEFAULT_MIN_RELAY_TX_FEE * vbytes) mod 1000 != 0` and `needed_fee` lands in `[floor(min), ceil(min))`, the check passes even though the transaction's effective fee rate is below the default minimum relay policy. The comment above the check states the intent to enforce "the default minimum fee rate," matching the doc-vs-code divergence pattern of the external report.

### Impact Explanation

The resulting `SignableTransaction` is passed to `TransactionMachine`/`TransactionSignMachine`, which produces a fully signed Taproot transaction via `taproot_key_spend_signature_hash` and FROST signature shares (`send.rs:373-397`). That signed transaction will be rejected at mempool admission for insufficient fee (`min relay fee not met`). For a vault/bridge wallet spending `ReceivedOutput`s, this means a threshold-signed spend that cannot propagate — payments are never delivered, and the coordinator must coordinate a new signing round (the signed transaction is already bound to specific prevouts via `Prevouts::All` and Schnorr signatures, so it cannot be fee-bumped without RBF signaling, which is disabled by `Sequence::MAX` at `send.rs:79`).

### Likelihood Explanation

The window is narrow (fees must fall in a sub-1-sat band per transaction), but `vbytes` is attacker/integrator-influenced through input count and output scripts, and `fee_per_vbyte` is a caller-supplied parameter. Any flow that computes a fee at exactly 1 sat/vB with a vsize not divisible by 1000 hits this for the saturating range.

### Recommendation

Round the minimum up instead of down:

```rust
let min_fee = (u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes).div_ceil(1000);
if needed_fee < min_fee {
  Err(TransactionError::TooLowFee)?;
}
```

Note: certainty is moderate — the divergence is only ~1 sat and exploitation requires the fee to land in the truncation band; this is borderline Medium/low impact. No stronger doc-vs-code minimum-bound analog was found in the in-scope crates within the investigation budget (the scanner's coinbase-maturity caveat is explicitly documented at `wallet/mod.rs:216-220`, and `DUST = 546` errs on the strict side for SegWit outputs).

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L206-213)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```
