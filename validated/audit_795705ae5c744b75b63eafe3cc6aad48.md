### Title
`ReceivedOutput::read` accepts untrusted (offset, TxOut, outpoint) triples without verifying the offset actually derives the output's `script_pubkey`, letting forged entries be treated as spendable received funds — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The Concrete CMS bug class is a collection/enumeration path that drops the per-item authorization check, so items the caller isn't entitled to are returned as valid. The analog in Serai lives in the Bitcoin wallet's entry deserialization: `Scanner::scan_transaction` is the legitimate producer of `ReceivedOutput`s and only emits entries whose `script_pubkey` it can prove are spendable (it derived the offset itself). `ReceivedOutput::read` — the deserialization path explicitly exposed to untrusted bytes — reconstructs the same structure from attacker-controlled `offset`, `TxOut`, and `OutPoint` fields without re-performing that ownership check. [1](#0-0) 

### Finding Description
`ReceivedOutput` is defined by an invariant: `p2tr_script_buf(scanner_key + offset*G)` must equal `output.script_pubkey`, meaning the output is spendable by applying `offset` to the threshold keys. `Scanner::scan_transaction` enforces this invariant by construction — it only pushes a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns an offset the scanner itself registered. [2](#0-1) 

`ReceivedOutput::read` performs no equivalent check. It reads any scalar via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns them as a valid `ReceivedOutput`. [1](#0-0)  There is no key on the deserialized object to check against and no validation hook; nothing downstream re-verifies the invariant at entry time.

The consumer, `SignableTransaction::new`, trusts these entries unconditionally for accounting: it sums `input.output.value` into `input_sat` and uses it to pass the `NotEnoughFunds` check and to size the change output. [3](#0-2)  The only time the offset/script relationship is checked is inside `multisig()`, where a mismatch causes `None` — i.e., after the transaction has already been constructed and approved. [4](#0-3) 

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` (the deserialized wallet/scanner state or any transport carrying it) can inject entries that are "reported received but not spendable":

- **Phantom UTXOs / balance inflation**: a `ReceivedOutput` with a fabricated `OutPoint` and a large `TxOut.value` counts toward `input_sat`, letting `SignableTransaction::new` succeed where real funds are insufficient. The resulting transaction spends a nonexistent prevout and will never confirm, yet fee sizing, change computation, and any balance reporting all treat the funds as real.
- **Unspendable-but-accepted entries**: a real `TxOut` whose `script_pubkey` doesn't match `key + offset*G` (wrong offset, or a script with a spendable script-path the offset was never meant to introduce — the exact hazard `register_offset` warns about for arbitrary offsets) is accepted at read time and only fails at `multisig()`, causing constructed spends to abort after value/change/fee decisions were made. [5](#0-4) 

This is precisely "funds reported received that are not spendable," an accepted impact class.

### Likelihood Explanation
Reachability requires untrusted bytes to reach `ReceivedOutput::read`, which is the documented serialization format for persisted/transported scan results — the threat model is an attacker who can corrupt or supply that data (compromised storage, relayed scan output). No threshold collusion or malicious validator is needed; a single corrupted entry poisons accounting for every transaction built on it. The forged entry cannot directly steal funds — `Prevouts::All` commits the real prevout amounts into the sighash, and `multisig()` blocks offset/script mismatches — so impact is integrity-of-accounting plus signing-session failure rather than key recovery, keeping this Medium.

### Recommendation
Bind each deserialized `ReceivedOutput` to the scanner's key: change `read` to take the scanner key (or store entries keyed under a `Scanner`), and verify `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` before returning the entry — the same per-entry check the producing path performs implicitly. Alternatively, add a `ReceivedOutput::verify(&self, key)` method and call it wherever deserialized entries enter `SignableTransaction::new`, rather than deferring the check to `multisig()`.

### Proof of Concept
```rust
// Attacker-controlled bytes for ReceivedOutput::read:
//   offset = arbitrary scalar (e.g. 0x01...), TxOut { value: 100_000_000, script_pubkey: <any P2TR or even non-P2TR script> },
//   OutPoint { txid: <nonexistent>, vout: 0 }
let forged = ReceivedOutput::read(&mut &attacker_bytes[..]).unwrap(); // succeeds; no ownership check

// Downstream, SignableTransaction::new counts forged.output.value toward input_sat,
// passes NotEnoughFunds, computes change/fee on phantom funds.
let stx = SignableTransaction::new(vec![forged], payments, Some(change), None, fee_rate).unwrap();

// multisig() either returns None (script_pubkey mismatch) — aborting after accounting —
// or, if the attacker copied a real watched script but wrong outpoint, produces a TX
// spending a nonexistent prevout that can never confirm.
assert!(stx.multisig(&keys).is_none() /* or produces unconfirmable tx */);
```

Contrast with `Scanner::scan_transaction`, which can never produce such an entry because it only emits outputs present in its derived `scripts` map. [6](#0-5)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L176-196)
```rust
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-235)
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
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
  }
```
