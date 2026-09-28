### Title
`SignableTransaction` trusts attacker-controlled `ReceivedOutput` amounts/outpoints — threshold signs an unbroadcastable transaction with fabricated value commitments - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to an ERC20 token that returns `true` on `transfer` without moving tokens, `SignableTransaction::new` trusts the *claimed* `TxOut` value and `OutPoint` carried inside each `ReceivedOutput` — a structure deserializable from untrusted bytes via `ReceivedOutput::read` — and never verifies the claimed prevout exists on-chain with that value or script. The claimed values are used directly for fee/change accounting and are committed into the BIP-341 sighash via `Prevouts::All`. A fabricated `ReceivedOutput` therefore causes the FROST threshold to produce a signature over a sighash committing to false input amounts, yielding a transaction that can never be accepted by the Bitcoin network, while the signing protocol reports success.

### Finding Description
`ReceivedOutput::read` deserializes `(offset: Scalar, output: TxOut, outpoint: OutPoint)` from an arbitrary byte stream with no validation whatsoever — the `TxOut` value and `script_pubkey` and the `OutPoint` are all attacker-supplied [1](#0-0) .

`SignableTransaction::new` then consumes these untrusted inputs. It computes `input_sat` purely from the *claimed* `input.output.value` fields, and uses that fabricated sum to (a) decide solvency (`NotEnoughFunds`), (b) compute the change output value via `input_sat - payment_sat - fee_with_change`, and (c) emit the change `TxOut` — all without any ground-truth check of the claimed amounts [2](#0-1) .

The only consistency check in `multisig` is that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — the *script* is checked, but the *value* and the *existence/ownership of the outpoint* are not [3](#0-2) .

Finally, `TransactionSignMachine::sign` signs each input with `cache.taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)` where `prevouts = Prevouts::All(&self.tx.prevouts)` — committing every claimed `TxOut` (including the fabricated `value`) into the signed message [4](#0-3) . `TransactionSignatureMachine::complete` returns `Ok(tx)` unconditionally once the Schnorr layer verifies [5](#0-4) .

This is the direct analog of the report: the code "returns true" (a completed `Transaction`) based on attacker-declared values rather than the actual balance/output on chain.

### Impact Explanation
An attacker who can feed crafted bytes to `ReceivedOutput::read` (or otherwise supply a `ReceivedOutput` to the signing path) can:

- **Cause the threshold to sign a permanently invalid transaction.** Because BIP-341 keyspend sighashes commit to all prevout amounts, any `ReceivedOutput` whose claimed `value` differs from the real UTXO produces a signature that every Bitcoin node rejects. `complete` still returns `Ok(Transaction)`, so the caller believes the spend succeeded — the analog of "funds reported received that are not spendable" / a signature produced over false premises.
- **Fabricate change and fee accounting.** An inflated claimed `value` makes `input_sat` pass solvency checks and creates a change output of up to the fabricated amount; a deflated or phantom outpoint inflates the effective fee beyond `needed_fee`. The multisig spends the session producing a TX that burns nothing real or that can never confirm, while a downstream caller may treat the burn/out-payment as finalized.

This is a liveness/integrity fault on the spend path: funds are reported as moved (a completed, "valid" signed transaction is returned) when they cannot be moved.

### Likelihood Explanation
Reachability requires attacker-controlled bytes to reach `ReceivedOutput::read`/`SignableTransaction::new` — explicitly in scope per the allowed untrusted-input surface. No key compromise, collusion, or malicious peer behavior beyond supplying the crafted bytes is needed. Severity is bounded to Medium: the forgery cannot steal funds (the sighash commitment makes a mismatched signature unspendable rather than redirectable), but it deterministically turns the honest threshold into a signer of unbroadcastable transactions and corrupts fee/change accounting for that session.

### Recommendation
Before signing, verify each `ReceivedOutput` against chain state: confirm `outpoint` resolves to an existing, unspent UTXO whose `value` and `script_pubkey` equal the claimed `TxOut` (e.g., via `gettxout`/prevout fetch in the producing layer, or by having the scanner record the value observed on chain and comparing in `SignableTransaction::new`). At minimum, add a defensive check that the claimed `TxOut` used for `Prevouts::All` and fee math is byte-identical to the output actually observed by `Scanner::scan_transaction`, rather than trusting deserialized values.

### Proof of Concept
1. Serialize a `ReceivedOutput` where `outpoint` names a real (or fake) UTXO paying to the multisig's `p2tr_script_buf`, `offset = Scalar::ZERO` (so `multisig`'s script check passes), but `output.value` is inflated (e.g., real output = 10,000 sats, claimed = 1,000,000 sats).
2. Feed the bytes through `ReceivedOutput::read` and pass the result to `SignableTransaction::new` with a payment of e.g. 500,000 sats and a change script. Construction succeeds (`input_sat = 1,000,000` passes `NotEnoughFunds`), a change output of ~500,000 sats is created.
3. `multisig` accepts (script matches), `sign` produces signature shares, `complete` returns `Ok(tx)`.
4. Broadcasting `tx` fails at every node: the BIP-341 sighash committed to a prevout amount of 1,000,000 while the real UTXO holds 10,000 — signature verification fails, and even ignoring that, `inputs < outputs` makes the transaction invalid. The signing round reported success; no funds actually moved.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-234)
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

    // Add the OP_RETURN output
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
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-284)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L413-428)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
  }
```
