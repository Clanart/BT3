### Title
Untrusted `ReceivedOutput` records can fabricate unspendable Bitcoin deposits - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` deserializes an offset, a `TxOut`, and an `OutPoint` without requiring proof that the referenced output exists in a confirmed Bitcoin block. [1](#0-0) 

`SignableTransaction::new` then trusts the deserialized output value as an input amount and stores the supplied `TxOut` as the claimed prevout. [2](#0-1) [3](#0-2) 

The multisig constructor only verifies that the claimed prevout script matches the threshold key after applying the supplied offset; it does not verify that the `OutPoint` exists or contains the claimed value. [4](#0-3) 

### Finding Description
An attacker who can supply bytes to `ReceivedOutput::read` can encode an arbitrary scalar offset, arbitrary satoshi amount, the victim multisig's normal P2TR script, and a fabricated `OutPoint`. [5](#0-4) 

The resulting object reports the attacker-selected amount through `ReceivedOutput::value`, even though no corresponding UTXO exists. [6](#0-5) 

Because `SignableTransaction::multisig` validates only `p2tr_script_buf(offset.group_key()) == prevout.script_pubkey`, a record naming the multisig's normal script with `offset = 0` passes the wallet-layer ownership check. [7](#0-6) 

The transaction builder subsequently creates a Taproot sighash committing to the fabricated prevout and requests a signature for it. [8](#0-7) 

### Impact Explanation
This lets an unprivileged party cause the wallet API to report funds as received that are not spendable. [9](#0-8) 

Any accounting or scheduling layer that treats a decoded `ReceivedOutput` as an authenticated chain observation can credit a nonexistent deposit, construct a transaction spending a nonexistent prevout, and cause threshold signers to process a spend that Bitcoin consensus will reject. [10](#0-9) [11](#0-10) 

This is a Medium-severity analog of trusting an off-chain authority for fund availability rather than proving that the claimed input is a mature confirmed UTXO. [1](#0-0) 

### Likelihood Explanation
The attacker only needs to control bytes passed to `ReceivedOutput::read`; serialization is fully attacker-controlled and has no block, Merkle proof, UTXO lookup, maturity check, or authentication tag. [1](#0-0) 

The affected path requires downstream code to accept decoded `ReceivedOutput` values as authoritative observations rather than values returned by `Scanner::scan_transaction` or `Scanner::scan_block`, since those functions derive the outpoint from an actual transaction. [12](#0-11) 

Exploitation is straightforward when such a boundary exists because the forged record needs only the multisig's public P2TR script and an arbitrary outpoint. [4](#0-3) 

### Recommendation
Do not treat `ReceivedOutput` deserialization as evidence of a confirmed spendable output. [1](#0-0) 

Authenticate decoded wallet records, or require callers to construct `ReceivedOutput` exclusively through `Scanner::scan_transaction` after validating block provenance, confirmation depth, and coinbase maturity. [12](#0-11) 

Before signing, resolve every `ReceivedOutput::outpoint` against the confirmed UTXO set and require the resolved `TxOut` to equal the claimed `output`, including amount and script. [2](#0-1) 

### Proof of Concept
The following attacker-controlled serialization fabricates a million-satoshi deposit to the multisig's ordinary P2TR address using `OutPoint::default`; `ReceivedOutput::read` accepts it, reports the forged value, and `multisig` accepts it because only the script is checked. [5](#0-4) [7](#0-6) 

```rust
use bitcoin::{
  Amount, OutPoint, TxOut,
  consensus::Encodable,
};
use ciphersuite::{group::ff::PrimeField, Ciphersuite};
use frost::curve::Secp256k1;
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

fn forged_received_output(
  keys: &frost::ThresholdKeys<Secp256k1>,
) -> Vec<u8> {
  // offset = 0, so the victim's normal P2TR key is used.
  let mut bytes = <Secp256k1 as Ciphersuite>::F::ZERO.to_repr().as_ref().to_vec();

  // Claim a large output paying the multisig's ordinary script.
  TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: p2tr_script_buf(keys.group_key()).unwrap(),
  }
  .consensus_encode(&mut bytes)
  .unwrap();

  // No such UTXO is required to exist.
  OutPoint::default().consensus_encode(&mut bytes).unwrap();

  bytes
}

fn accepts_unspendable_output(
  keys: &frost::ThresholdKeys<Secp256k1>,
) {
  let encoded = forged_received_output(keys);
  let received = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

  // The wallet reports attacker-controlled value.
  assert_eq!(received.value(), 1_000_000);

  // The transaction builder trusts the fabricated prevout.
  let signable = SignableTransaction::new(
    vec![received],
    &[],
    Some(p2tr_script_buf(keys.group_key()).unwrap()),
    None,
    1,
  )
  .unwrap();

  // This passes because only the prevout script is compared.
  assert!(signable.multisig(keys).is_some());
}
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L115-134)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-227)
```rust
  /// Scan a transaction.
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

  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-220)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
