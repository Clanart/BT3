### Title
Untrusted `ReceivedOutput` bytes let an attacker forge prevout values, causing signers to sign a sighash committing to false input amounts - (File: networks/bitcoin/src/wallet/mod.rs, networks/bitcoin/src/wallet/send.rs)

### Summary
The webhook-bypass class in the reference report is "verification logic trusting attacker-supplied context that should come from a trusted source." The analog in Serai's Bitcoin wallet is `SignableTransaction`: the `TxOut` (including its `value`) carried inside `ReceivedOutput` is treated as authoritative transaction data and is fed directly into fee arithmetic and into `Prevouts::All` for the Taproot sighash. `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` entirely from untrusted bytes with no consistency check that the claimed `TxOut`/`outpoint` correspond to a real on-chain output, and `multisig()` only validates the `script_pubkey` — never the value. An attacker who can feed crafted bytes to `ReceivedOutput::read` (or otherwise inject a forged `ReceivedOutput`) can make honest signers produce threshold signatures over a sighash committing to prevout amounts the attacker chose.

### Finding Description
`Scanner::scan_transaction` produces `ReceivedOutput` by matching `output.script_pubkey` and copying the whole `TxOut` — that path is safe because the data comes from the chain [1](#0-0) . However, `ReceivedOutput::read` offers a second provenance channel: it consensus-decodes an arbitrary `TxOut` and `OutPoint` plus an arbitrary `offset` scalar from a byte stream, with zero validation [2](#0-1) . `SignableTransaction::new` then trusts `input.output.value` for `input_sat` (fee/funding math) and stores `input.output` as `prevouts` [3](#0-2) . In `multisig()`, the only authenticity check is `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — the *value* field is never checked against anything [4](#0-3) . Finally, `TransactionSignMachine::sign` commits those attacker-controlled prevouts into the signature message via `Prevouts::All` and `taproot_key_spend_signature_hash` [5](#0-4) .

An attacker crafts bytes such that `offset` + `output.script_pubkey` form a consistent pair matching an offset-registered key (e.g., by copying the script of a real deposit output to the base address, which the attacker can compute since the group key and `register_offset` scripts are public/derivable), while setting `output.value` to any amount and `outpoint` to anything. The script check passes; the forged value flows into the sighash.

### Impact Explanation
Every honest participant running `sign` produces a signature share over a Taproot sighash that commits to prevout values and outpoints that do not match any real UTXO — i.e., the threshold group signs a message it never intended to sign. Depending on the forged values, the arithmetic in `SignableTransaction::new` can also be skewed (inflated `input_sat` bypasses `NotEnoughFunds`; deflated `input_sat` suppresses or removes the change output). Because BIP-341 key-path signature verification re-derives the sighash from the *actual* prevouts of the transaction being validated, signatures produced under forged prevout values will not validate for any real on-chain spend, so a forged-`ReceivedOutput` signer set can be induced to emit signatures that are worthless on-chain yet cryptographically valid under the group key for the attacker's chosen sighash. Severity: Medium — the signers sign an unintended message, but the forged values cannot be turned into a valid spend of real funds on their own.

### Likelihood Explanation
Reachable by an unprivileged party wherever `ReceivedOutput` is transported or persisted across a trust boundary (the crate exposes `read`/`write`/`serialize` specifically for this) [6](#0-5) . It requires no control of signers and no threshold collusion — only the ability to supply bytes to `ReceivedOutput::read` or inject a forged `ReceivedOutput` into whatever pipeline feeds `SignableTransaction::new`. The main mitigating factor is that exploitation requires an integrator that obtains `ReceivedOutput`s over an unauthenticated channel rather than from its own `Scanner` over verified blocks, which is plausible but not guaranteed.

### Recommendation
Bind the `TxOut`/`outpoint` to reality before signing: in `SignableTransaction::new` or `TransactionSignMachine::sign`, verify each input's `output` against the actual output fetched from a trusted chain view (e.g., `bitcoin::core_rpc` lookup by `outpoint`), or at minimum require the caller to pass prevouts proven to be on-chain rather than trusting the serialized `TxOut` inside `ReceivedOutput`. If serialization is only intended for locally-scanned outputs, document that `ReceivedOutput::read` MUST NOT be used on untrusted bytes, mirroring the upstream fix's "ignore forwarded headers unless explicitly trusted" resolution.

### Proof of Concept
```rust
// Attacker knows the group key G (public) and a registered offset script.
// Let offset_real be a registered offset (e.g., Scalar::ZERO for the base address)
// and script = p2tr_script_buf(G + offset_real*Ggen).

let forged = {
  let mut buf = Vec::new();
  // offset consistent with the script
  buf.extend(offset_real.to_bytes());
  // TxOut with the real script_pubkey but an attacker-chosen inflated value
  TxOut { value: Amount::from_sat(u64::MAX / 2), script_pubkey: script.clone() }
    .consensus_encode(&mut buf).unwrap();
  // arbitrary outpoint
  OutPoint::new(Txid::from_byte_array([0xAA; 32]), 0)
    .consensus_encode(&mut buf).unwrap();
  buf
};

let ro = ReceivedOutput::read(&mut &forged[..]).unwrap(); // succeeds, no checks

// Honest side:
let stx = SignableTransaction::new(vec![ro], &payments, Some(change), None, fee_rate).unwrap();
// input_sat = u64::MAX/2 -> NotEnoughFunds bypassed, huge change computed
let machine = stx.multisig(&keys).unwrap(); // passes: only script_pubkey checked
// ... preprocess, sign ...
// TransactionSignMachine::sign commits Prevouts::All(prevouts) into the sighash,
// so each signer's share signs a message asserting the input is worth u64::MAX/2.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-148)
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

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-255)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

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
