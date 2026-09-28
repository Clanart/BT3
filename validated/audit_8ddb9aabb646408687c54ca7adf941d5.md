### Title
Duplicate `ReceivedOutput`s are not rejected, letting `SignableTransaction::new` count already-committed funds twice and overestimate the spendable balance - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` sums `input_sat` over the caller-supplied `inputs: Vec<ReceivedOutput>` without checking that the outpoints are distinct. The same UTXO can be listed twice (or more), inflating `input_sat` — analogous to the Truflation bug where `balanceOf` included tokens already committed to prior rewards, the solvency check here counts value that is already committed by an earlier `TxIn` to the same outpoint. The `NotEnoughFunds` check then passes, and payments/change are sized against funds that do not exist.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the constructor builds the spend plan directly from the input list:

```rust
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
...
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { .. })?;
}
``` [1](#0-0) [2](#0-1) 

No deduplication on `input.outpoint` is performed anywhere in `new`. Each duplicate entry produces an additional `TxIn` referencing the same `previous_output`, and its `value` is added to `input_sat` again. The change calculation has the same flaw:

```rust
if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
  if value >= DUST {
    tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
``` [3](#0-2) 

`ReceivedOutput` is deserialized from untrusted bytes via `ReceivedOutput::read`, which reads an offset, `TxOut`, and `OutPoint` verbatim with no uniqueness or ownership validation: [4](#0-3) 

Because Taproot key-spend sighashes commit to `Prevouts::All` and each input's `previous_output`, the resulting transaction is still fully signed by the FROST multisig path (`TransactionSignMachine::sign` → `TransactionSignatureMachine::complete`), producing a transaction that Bitcoin consensus rejects as a double-spend within a single block — or, if the duplicated entries are crafted so only one inclusion is valid, an overpayment/oversized-change commitment the group signs for value it does not control. [5](#0-4) 

### Impact Explanation
The solvency invariant of `SignableTransaction::new` ("`sum(inputs)` covers `payments + fee`") is violated by double-counting. Consequences:

- The threshold group signs a transaction spending more than it owns: consensus-invalid, so any payment built on it can never confirm while signatures/commitments were still consumed (and outputs may be treated as pending by watchers).
- If only one copy of the duplicated outpoint is valid, `payments`/`change` are still committed at the inflated total, creating a signed transaction whose economic value exceeds available funds — mirroring the reward-depletion outcome in the report, where the inflated `rewardRate` drained funds earmarked elsewhere.

### Likelihood Explanation
Reachable whenever the input list is assembled from deserialized `ReceivedOutput`s (e.g., via `ReceivedOutput::read` on bytes from an untrusted source) rather than exclusively from a trusted local `Scanner`. There is no defensive check; the bug triggers with a single duplicated entry. Severity is bounded to Medium because the main effect is a consensus-invalid transaction / mis-committed funds rather than direct theft: the multisig only signs the malformed transaction, it does not reveal key material.

### Recommendation
In `SignableTransaction::new`, reject duplicate outpoints before summing `input_sat`:

```rust
{
  let mut seen = std::collections::HashSet::new();
  for input in &inputs {
    if !seen.insert(input.outpoint) {
      Err(TransactionError::DuplicateInput)?;
    }
  }
}
```

This enforces that every satoshi counted toward `input_sat` is backed by a distinct UTXO — the same fix pattern as subtracting already-committed amounts (`balance -= totalToPay - totalPaid`) in the original report.

### Proof of Concept
```rust
// One real scanned output worth V sats
let output: ReceivedOutput = scanner.scan_transaction(&tx).swap_remove(0);

// Submit it twice; both clones carry the same outpoint + value
let inputs = vec![output.clone(), output];

// NotEnoughFunds is bypassed: input_sat == 2*V while only V is spendable
let payments = vec![(addr(), V + 1000)]; // more than the real balance
let stx = SignableTransaction::new(inputs, &payments, None, None, FEE);
assert!(stx.is_ok()); // succeeds; produces a self-double-spending transaction
```
The resulting transaction, once signed by the FROST `TransactionMachine`, contains two `TxIn`s referencing the same `previous_output` and is rejected by the Bitcoin network as a double-spend — i.e., the wallet committed to paying funds it does not have.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
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

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
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
