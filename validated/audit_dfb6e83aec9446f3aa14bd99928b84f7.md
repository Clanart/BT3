### Title
`SignableTransaction::new` computes fee and weight over `payments` while omitting the OP_RETURN `data` output, producing an under-priced / potentially non-standard transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

Analogous to the report's "wrong quantity used where a scaled value is required" class, `SignableTransaction::new` calculates the transaction's weight and vsize from a *different set of outputs* than the transaction actually commits to. The fee and the standard-weight check are computed over `payments` (plus optional change), but the signed transaction additionally contains an OP_RETURN output carrying up to 80 bytes of attacker-influenced `data`. The result is an incorrect fee/vsize formula: the paid fee rate and the `MAX_STANDARD_TX_WEIGHT` check are both evaluated on a smaller transaction than the one produced.

### Finding Description

In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` before the weight calculation:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...)
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
``` [1](#0-0) 

`calculate_weight_vbytes` builds a mock `Transaction` whose outputs are only `payments` (plus change when provided). The OP_RETURN output in `tx_outs` is never included, so `vbytes`, `needed_fee`, the minimum-fee check, and the later `weight > MAX_STANDARD_TX_WEIGHT` check are all evaluated against a strictly smaller transaction than the one returned in `self.tx`: [2](#0-1) [3](#0-2) 

With `data` of the permitted maximum 80 bytes (`data.len() > 80` is rejected), the unaccounted OP_RETURN output adds roughly 90 serialized bytes (~360 WU, ~90 vbytes) that are never priced: [4](#0-3) 

The signed transaction itself is then produced by `TransactionSignMachine::sign` / `TransactionSignatureMachine::complete` over this exact `tx`, so the mis-priced transaction is what gets broadcast: [5](#0-4) [6](#0-5) 

### Impact Explanation

Two concrete failure modes, both reachable by an unprivileged party who supplies `data` destined for a signed transaction (transaction data they cause to be signed):

1. **Effective fee below intent/policy.** `needed_fee` is `fee_per_vbyte * vbytes` computed without the OP_RETURN output. The actual fee paid (`sum(inputs) - sum(outputs)`) is correct in absolute terms, so the *effective* feerate is `needed_fee / (vbytes + ~90)`, lower than the requested `fee_per_vbyte`. At low `fee_per_vbyte` this can drop the real feerate below the mempool minimum (`DEFAULT_MIN_RELAY_TX_FEE` check uses the same undercounted `vbytes`), causing the transaction to be rejected from relay while the wallet believes it paid the requested rate — outputs tracked via `txid()` never confirm and the funds are not spendable until the transaction is rebuilt.

2. **Standard-weight check bypass.** `weight` omits the OP_RETURN output, so a transaction at the boundary can pass `weight <= MAX_STANDARD_TX_WEIGHT` while the real transaction exceeds it, producing a non-standard transaction that nodes will not relay.

Severity: Medium — bounded mispricing (~90 vbytes per transaction) and a liveness/relaying failure rather than direct theft, but the formula is objectively incorrect on public, caller-supplied input.

### Likelihood Explanation

Requires a transaction carrying `data` (an OP_RETURN output) — i.e., any consumer of this API that attaches metadata — plus feerate/weight conditions near a policy boundary. The omission is deterministic whenever `data` is `Some`, so any such transaction is systematically under-priced and the weight check is systematically weak.

### Recommendation

Compute `weight`/`vbytes` over the actual output set. Build `tx_outs` first (payments + OP_RETURN + tentative change), then call `calculate_weight_vbytes` with the full `tx_outs`-equivalent list — e.g., extend `calculate_weight_vbytes` to take `&[TxOut]` or add the OP_RETURN `TxOut` into the mock transaction — so that `needed_fee`, the minimum-fee check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction actually signed. Re-check `DUST`/standardness on the final transaction, and add a test asserting `needed_fee == fee_per_vbyte * real_vsize` for transactions including `data` and `change`.

### Proof of Concept

```rust
// Conceptual: construct two SignableTransactions identical except `data`.
let payments: &[(ScriptBuf, u64)] = &[(some_script.clone(), 100_000)];

let no_data = SignableTransaction::new(
    inputs.clone(), payments, change.clone(), None, fee_per_vbyte,
).unwrap();

let with_data = SignableTransaction::new(
    inputs.clone(), payments, change.clone(),
    Some(vec![0xAA; 80]), // max permitted, passes the <= 80 check
    fee_per_vbyte,
).unwrap();

// Both report the same needed_fee because the OP_RETURN output was
// never included in calculate_weight_vbytes:
assert_eq!(no_data.needed_fee(), with_data.needed_fee());

// Yet with_data's real transaction is ~90 bytes / ~360 WU larger:
assert!(
    with_data.transaction().weight().to_wu() >
    no_data.transaction().weight().to_wu()
);

// Therefore the effective feerate of with_data is strictly below
// fee_per_vbyte, and a boundary transaction can exceed
// MAX_STANDARD_TX_WEIGHT while the check on the internal `weight`
// variable passed.
```

The discrepancy is fully deterministic: `calculate_weight_vbytes` is invoked with `payments` at `send.rs:204` and `send.rs:226`, while the OP_RETURN `TxOut` is pushed only into `tx_outs` at `send.rs:194-202` and never participates in either weight calculation or the final `weight > MAX_STANDARD_TX_WEIGHT` check at `send.rs:241`.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
```rust
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-212)
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

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-255)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }

    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-427)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
```
