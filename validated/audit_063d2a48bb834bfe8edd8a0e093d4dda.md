### Title
`SignableTransaction::new` computes the fee and weight without the `OP_RETURN` data output, underpaying the requested fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a computed claim/value that ignores a fee deducted at settlement. The analog in Serai's `bitcoin-serai` wallet is the inverse of the same accounting error: `SignableTransaction::new` computes `needed_fee` from a transaction weight that omits the variable-length `OP_RETURN` output, so the signed transaction pays less than `fee_per_vbyte` for its true size.

### Finding Description
`SignableTransaction::new` builds `tx_outs` including the `OP_RETURN` output when `data` is supplied [1](#0-0) . However, the weight/vbyte estimate used for `needed_fee` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which constructs a dummy transaction from `payments` only — the `data` output is never included [2](#0-1) .

The same omission occurs in the change-output branch: `calculate_weight_vbytes` is again called with `payments` (not `tx_outs`), so `vbytes_with_change` and `fee_with_change` also exclude the `OP_RETURN` output [3](#0-2) .

Additionally, the `MAX_STANDARD_TX_WEIGHT` check uses the underestimated `weight`, so a transaction near the limit with an 80-byte `data` payload can pass the check while the real transaction exceeds it [4](#0-3) .

### Impact Explanation
The actual fee is `sum(inputs) - sum(outputs)`, which equals `needed_fee` (or `fee_with_change` when change is kept) [5](#0-4) . Since that fee was priced for a smaller transaction, the real feerate is `fee_per_vbyte * vbytes_estimated / vbytes_actual < fee_per_vbyte`. With `data` up to 80 bytes, the OP_RETURN output adds ~92 vbytes of unaccounted size. Depending on `fee_per_vbyte`, the transaction can fall below the mempool's minimum relay feerate or confirm far slower than intended — and because FROST preprocesses are bound to this exact transaction (caching is `unimplemented`), a stuck/underpriced transaction cannot simply be re-signed at a higher rate without RBF-style replacement [6](#0-5) . This is a fee-accounting discrepancy in production fund-movement code reachable whenever the coordinator embeds `data` in a withdrawal.

### Likelihood Explanation
`SignableTransaction::new` accepts `data: Option<Vec<u8>>` (up to 80 bytes) from its caller; any Serai flow that attaches plan/metadata data to a Bitcoin spend triggers the underpriced fee deterministically. No malicious participant or validator is required — only a caller supplying `data` to a public constructor.

### Recommendation
Include the `data` output in the size estimate: either pass the fully built `tx_outs` (payments + OP_RETURN) into `calculate_weight_vbytes`, or add the serialized size of the OP_RETURN output (`8 + compact_size(script len) + 1 + 1 + data.len()` weight-equivalent) to the computed weight before deriving `vbytes`, in both the no-change and change branches. Then recompute `weight` for the `MAX_STANDARD_TX_WEIGHT` check on the actual output set.

### Proof of Concept
Conceptual: call `SignableTransaction::new` with one input of `payment + fee + margin` sats, `payments = [(addr, P)]`, `data = Some(vec![0; 80])`, `change = None`, `fee_per_vbyte = F`. The resulting `needed_fee()` equals `F * v` where `v` excludes the ~90-vbyte OP_RETURN output, while `fee()` (the real fee) equals `needed_fee`. The effective feerate is `F * v / (v + ~92) < F`, so a transaction intended to meet relay minimums pays strictly less than specified — the exact analog of an amount computed without the fee that will actually be charged.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-202)
```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-204)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

**File:** networks/bitcoin/src/wallet/send.rs (L225-232)
```rust
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L333-349)
```rust
  fn cache(self) -> CachedPreprocess {
    unimplemented!(
      "Bitcoin transactions don't support caching their preprocesses due to {}",
      "being already bound to a specific transaction"
    );
  }

  fn from_cache(
    (): (),
    _: ThresholdKeys<Secp256k1>,
    _: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    unimplemented!(
      "Bitcoin transactions don't support caching their preprocesses due to {}",
      "being already bound to a specific transaction"
    );
  }
```
