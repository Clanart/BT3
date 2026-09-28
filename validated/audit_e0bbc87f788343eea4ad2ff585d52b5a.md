### Title
`SignableTransaction::new` overflows `payment_sat` / `input_sat < payment_sat + needed_fee`, letting oversized outputs slip past the `NotEnoughFunds` check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The auction bug class is "an accumulator that is allowed to exceed its cap, breaking a later subtraction/comparison". `SignableTransaction::new` sums attacker-influenced payment amounts into `u64` saturating-less, then performs `input_sat < (payment_sat + needed_fee)`. Both the `sum()` and the `+` are plain `u64` arithmetic, so crafted payments wrap the comparison and produce a transaction whose outputs exceed its inputs, and `fee()` underflows.

### Finding Description
In `SignableTransaction::new`, per-payment validation only checks `*amount < DUST` (546 sats); there is no bound on the individual amount or count beyond `TooLargeTransaction` on the final weight. [1](#0-0) [2](#0-1) [3](#0-2) 

`payment_sat` is computed as `payments.iter().map(|payment| payment.1).sum::<u64>()` — an unchecked `u64` sum. Two payments such as `(script, u64::MAX - 1000)` and `(script, u64::MAX - 1000)` each pass the `DUST` check, and their sum wraps to a small value. The funds check `input_sat < (payment_sat + needed_fee)` then sees a tiny `payment_sat` (the `+` can itself also wrap), so `NotEnoughFunds` is not raised. `tx_outs` are built with the real `u64::MAX`-scale amounts, so the resulting `Transaction` has outputs vastly exceeding its inputs — invalid on the network — while `needed_fee`/`fee()` report a wrapped, meaningless fee: [4](#0-3) 

The same unchecked arithmetic applies to `fee_per_vbyte * vbytes` (line 206) and `fee_per_vbyte * vbytes_with_change` (line 227): a large `fee_per_vbyte` wraps `needed_fee` to a small value, so an absurdly high caller-specified fee rate produces a transaction paying far less than intended, or vice versa causes `payment_sat + fee_with_change` to wrap in the `checked_sub` change branch — making the reported `needed_fee` inconsistent with the actual fee implied by `sum(inputs) - sum(outputs)`, whose subtraction at line 139-140 can itself underflow.

Payments to this constructor are derived from user-initiated burn/withdrawal instructions reaching the processor, so the amounts are attacker-influenced rather than trusted constants.

### Impact Explanation
An attacker can cause a `SignableTransaction` to be constructed (and subsequently threshold-signed via `multisig()`/`TransactionSignMachine::sign`) whose outputs exceed its inputs — an invalid transaction that wastes the multisig's selected UTXOs' fee budget intent and, worse, `fee()` underflows `sum(inputs) - sum(outputs)`, panicking under overflow-checks or returning a wrapped fee. It also permits a signed transaction paying a fee far below the requested rate (unconfirmable, stuck funds) when `fee_per_vbyte * vbytes` wraps, mirroring the report's "preview fails / accounting exceeds cap" class.

### Likelihood Explanation
Exploitation requires the caller to supply payments whose total overflows `u64` or a large `fee_per_vbyte`. If the processor enforces sane payment bounds upstream, reachability is reduced; within the `bitcoin-serai` wallet crate itself there is no such guard, and the constructor accepts arbitrary `(ScriptBuf, u64)` pairs. Medium likelihood; Medium severity (DoS / invalid transaction / misleading fee reporting rather than direct theft, since an outputs>inputs transaction is consensus-invalid).

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new`:
- `payment_sat = payments.iter().try_fold(0u64, |acc, p| acc.checked_add(p.1))` and error on overflow.
- `input_sat.checked_add`/`payment_sat.checked_add(needed_fee)` for the funds check, and `fee_per_vbyte.checked_mul(vbytes)` for fee computation.
- Compute `fee()` with `checked_sub` and return an error instead of subtracting raw.

### Proof of Concept
```rust
// networks/bitcoin: payments individually >= DUST but their sum wraps u64
let huge = u64::MAX - 1000; // >= DUST
let payments = vec![(p2tr_script_buf(key).unwrap(), huge),
                    (p2tr_script_buf(key).unwrap(), huge)];
// payment_sat wraps to (2*huge) mod 2^64 = a small number;
// input_sat < payment_sat + needed_fee is false -> NotEnoughFunds skipped.
let stx = SignableTransaction::new(inputs, &payments, None, None, FEE).unwrap();
// tx outputs total ~2*u64::MAX satoshis over the real inputs:
// stx.fee() computes sum(inputs) - sum(outputs) -> u64 underflow
// (panic with overflow-checks, garbage fee otherwise).
```
The panic/wrap at `fee()` (send.rs:139-141) and the bypassed check at send.rs:215 demonstrate the same "accumulator exceeds cap then breaks later arithmetic" failure as the Knox `_previewWithdraw` overflow.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-191)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```
