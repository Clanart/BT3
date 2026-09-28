### Title
Unchecked `u64` overflow in `SignableTransaction::new` payment/fee summation bypasses the `NotEnoughFunds` check and produces a threshold-signed transaction that is invalid and unbroadcastable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
CVE-2016-2105 is an integer-overflow-in-length bug class: an arithmetic computation over attacker-influenced sizes silently wraps, and the wrapped value is then used as a bound/size decision. The direct analog in Serai is in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`, where `payment_sat` and `payment_sat + needed_fee` are computed with plain `u64` addition over payment amounts that originate from untrusted cross-chain OutInstruction data. There is no per-payment upper bound check (only a `DUST` lower bound), so payments summing past `u64::MAX` wrap the sufficiency test and cause a transaction whose outputs exceed its inputs to be constructed and threshold-signed.

### Finding Description
In `SignableTransaction::new`, `payment_sat` accumulates all requested payment amounts with `sum::<u64>()` [1](#0-0) . The funds-sufficiency check is `input_sat < (payment_sat + needed_fee)` [2](#0-1) . Both operations are unchecked. Each payment amount is validated only against the `DUST` lower bound [3](#0-2) ; nothing caps a payment at Bitcoin's 21M-coin supply or prevents their sum from exceeding `u64::MAX`.

An unprivileged party triggers this path because the payments vector is built from OutInstruction/scheduler data derived from user-submitted bridge instructions (amounts and target scripts are user data), and the resulting `SignableTransaction` is what gets signed by `TransactionSignMachine::sign` / `TransactionSignatureMachine::complete`, which attach Schnorr witnesses to every input [4](#0-3) . The untrusted byte path is: attacker instruction → payment `(ScriptBuf, u64)` entries → `SignableTransaction::new` → FROST signature over the overflow-built transaction.

If `payment_sat` overflows to a small value (release mode) or `payment_sat + needed_fee` wraps, `input_sat` (bounded by real on-chain UTXO values) passes the comparison. The change logic uses `checked_sub` [5](#0-4) , so no change output is added, and the final transaction with `output.value > sum(inputs)` is signed anyway. In debug builds the overflow panics instead.

### Impact Explanation
The validator set threshold-signs a Bitcoin transaction that consensus will never accept (`output sum > input sum`). This is signing of an unintended/invalid message: the FROST protocol completes and yields `Transaction` bytes that waste a signing session and cannot spend the committed UTXOs. Repeated submission stalls the signing pipeline for legitimate payouts. In debug builds the panic crashes the processor task handling the plan.

### Likelihood Explanation
Reachable by any user who can cause a payment to be scheduled (bridge withdrawal/OutInstruction flow), which requires no validator privilege — exactly the "messages or transaction data they cause to be signed" surface. Exploitation needs payment amounts summing near `u64::MAX`, which no code path rejects since only the `DUST` floor is enforced.

### Recommendation
Validate each payment amount against Bitcoin's `MAX_MONEY` bound and compute `payment_sat` and `payment_sat + needed_fee` with `checked_add`, returning `NotEnoughFunds`/`TooMuchData` on overflow. Similarly use `checked_mul` for `fee_per_vbyte * vbytes` [6](#0-5) .

### Proof of Concept
Conceptual invocation of the in-scope API:

```rust
// networks/bitcoin/src/wallet/send.rs
let inputs = vec![ReceivedOutput { /* a real 1-BTC P2TR output */ .. }];
let payments = &[
  (attacker_script_a.clone(), u64::MAX - 1000),  // each >= DUST, so accepted
  (attacker_script_b.clone(), u64::MAX - 1000),  // payment_sat wraps to ~1998
];
let tx = SignableTransaction::new(inputs, payments, None, None, 1).unwrap();
// payment_sat + needed_fee wrapped below input_sat -> NotEnoughFunds never fires
// tx.multisig(keys) ... sign() ... complete() produces a signature on a
// transaction whose outputs (~2*u64::MAX sats) exceed its inputs (~1e8 sats)
```

The completed `Transaction` is a validly-FROST-signed artifact committing the multisig to impossible outputs, matching the CVE's "wrapped length used as a bound" shape mapped onto Serai's sum-as-bounds-check.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
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

**File:** networks/bitcoin/src/wallet/send.rs (L228-234)
```rust
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-398)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
  }
```
