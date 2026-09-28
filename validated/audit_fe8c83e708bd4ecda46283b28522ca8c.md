### Title
Unchecked u64 arithmetic in `SignableTransaction::new` lets attacker-supplied payment amounts wrap past the `NotEnoughFunds` check — the threshold signs a transaction with fabricated economics (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The Kaoyaswap incident was caused by faulty accounting logic in its `swap` function: output was granted against an input check that did not correctly measure what was actually paid in. The same bug class exists in Serai's in-scope Bitcoin spend path. `SignableTransaction::new` — the function that decides whether the FROST wallet "has enough input" to fund a set of payments, i.e. the swap-equivalent solvency check — performs all its value accounting with unchecked `u64` `sum()` and `+` operations, so attacker-chosen payment amounts wrap modulo `2^64` and defeat the `NotEnoughFunds` guard.

### Finding Description
`SignableTransaction::new` sums the claimed input values and the requested payments with wrapping arithmetic [1](#0-0) . The solvency check then uses an unchecked addition [2](#0-1) :

```rust
let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
...
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { ... })?;
}
```

Both `payment_sat` (a `sum::<u64>()`) and `payment_sat + needed_fee` wrap in release builds. An unprivileged party who controls the `payments: &[(ScriptBuf, u64)]` list (e.g. withdrawal/request inputs fed into this constructor) can choose two payments whose amounts sum past `u64::MAX`, e.g. `u64::MAX - k` and `k + small`. `payment_sat` wraps to `small`, so `input_sat < payment_sat + needed_fee` is false and the `NotEnoughFunds` guard is bypassed even though the declared outputs total ~`2^64` sats against real inputs.

The same unchecked pattern repeats in the change computation [3](#0-2) : `input_sat.checked_sub(payment_sat + fee_with_change)` subtracts the *wrapped* sum, so it succeeds and appends a change output of nearly `input_sat`, making the transaction look internally consistent to the wallet. Only the per-payment `< DUST` check constrains each individual `payment.1`; nothing bounds their sum [4](#0-3) . `input_sat` itself is a wrapping sum over attacker-influenceable `ReceivedOutput` values (`TxOut.value` is `u64` and is accepted verbatim by `ReceivedOutput::read` from untrusted bytes) [5](#0-4) [6](#0-5) .

The resulting `SignableTransaction` proceeds through `multisig`, `preprocess`, and `sign`, producing real BIP-340 FROST signature shares over the wrapped-economics sighash (`Prevouts::All` commits to the attacker-shaped output set) [7](#0-6) .

### Impact Explanation
Analogous to Kaoyaswap (swap granted output because the input-side accounting was faulty), the wallet's spend-construction logic attests to solvency that does not exist and drives the threshold network to sign a transaction whose declared inputs and outputs do not match — a concrete case of the multisig signing an unintended message. The signed transaction is consensus-invalid (outputs exceed real inputs), so it cannot steal funds on-chain, but it corrupts the wallet's fee/change accounting (`fee()` then underflows when queried), consumes preprocess/nonce material across all signers, and silently produces a permanently unbroadcastable transaction — stalling the affected UTXOs' withdrawal pipeline and burning a distributed signing round on demand. Medium: reliably reachable accounting flaw with concrete unintended-signature impact, but no direct fund extraction.

### Likelihood Explanation
Reachable by any unprivileged party able to specify payment amounts/scripts in requests routed to this constructor — no validator, collusion, or key access needed. Triggering it requires only two `u64` payment values whose sum exceeds `2^64 - 1`; all other inputs are attacker-chosen or ignored. Not a probabilistic or timing issue.

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new`: `payment_sat` should be computed with `checked_add`/`try_fold` returning `TransactionError::NotEnoughFunds` or a dedicated `Overflow` variant, `input_sat` likewise, and `payment_sat + needed_fee` / `payment_sat + fee_with_change` replaced with `checked_add` that errors on overflow. Optionally cap each payment at `MAX_MONEY` (21e14 sats) alongside the `DUST` lower bound.

### Proof of Concept
```rust
// Attacker-controlled payments list passed to SignableTransaction::new.
// Each payment individually passes the `*amount < DUST` check (send.rs:165-169).
let script = ScriptBuf::new_p2tr_tweaked(/* any tweaked key */);
let payments = vec![
  (script.clone(), u64::MAX - 1_000),  // huge payment #1
  (script.clone(), 2_000),             // payment #2 pushes the sum over 2^64
];
// payment_sat wraps to 2_000 - 1_000 - 1 = 999 (mod 2^64)
// Real inputs, e.g. a single ReceivedOutput worth 1_000_000 sats:
//   input_sat = 1_000_000
// Check at send.rs:215: 1_000_000 < 999 + needed_fee  -> false
//   => NotEnoughFunds is BYPASSED despite ~1.8e19 sats of outputs
// Change path (send.rs:224-235): input_sat.checked_sub(wrapped_sum + fee)
//   succeeds, emitting a ~1_000_000-sat change output, so the tx is built
//   and handed to `multisig()`/`sign()` for FROST signing of the
//   sighash committing to the bogus output set (send.rs:373-397).
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-191)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```
