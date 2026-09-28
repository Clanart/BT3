### Title
Untrusted `ReceivedOutput` deserialization accepts nonexistent UTXOs, causing signatures for unspendable transactions - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts a caller-supplied scalar offset, `TxOut`, and `OutPoint` without proving that the referenced outpoint exists or that it was discovered by scanning a confirmed Bitcoin transaction. [1](#0-0) 

The deserialized object is treated as spendable by `SignableTransaction::new`, which uses its value as available input value, its outpoint as a transaction input, and its `TxOut` as a prevout committed by the Taproot sighash. [2](#0-1) [3](#0-2) 

### Finding Description
The report's bug class is unsafe consumption of serialized attacker-controlled objects. The analogous Serai path is not code execution through pickle, but semantic deserialization of an attacker-supplied Bitcoin output into a trusted spendable-output type.

`ReceivedOutput::read` performs only syntactic checks: it reads a canonical secp256k1 scalar with `Secp256k1::read_F`, then consensus-decodes a `TxOut` and `OutPoint`. [4](#0-3) 

It does not verify that the outpoint references an existing confirmed transaction output, that the transaction is in a block, or that the output was produced by `Scanner::scan_transaction`. [5](#0-4) 

`SignableTransaction::new` subsequently trusts `input.output.value` when calculating `input_sat`, trusts `input.outpoint` when constructing `tx.input`, and stores the supplied `TxOut` in `prevouts`. [6](#0-5) [3](#0-2) 

During signing, every stored prevout is committed with `Prevouts::All`, so the fabricated output's amount and script are part of each Taproot signature message. [7](#0-6) 

### Impact Explanation
An unprivileged party that can supply serialized `ReceivedOutput` bytes can fabricate an apparently spendable input by pairing a legitimate Serai-controlled `script_pubkey` with a nonexistent `txid:vout`.

If the output uses the wallet's ordinary P2TR script and offset zero, `multisig` accepts it because `p2tr_script_buf(offset.group_key())` equals the supplied `script_pubkey`; the bogus outpoint is not checked. [8](#0-7) 

This can cause threshold signers to authorize a transaction whose input does not exist and therefore can never be valid on Bitcoin. It can also inflate `input_sat` with a nonexistent amount, making the wallet treat unavailable funds as spendable and burn legitimate inputs as fees if the fabricated amount is combined with real inputs. [9](#0-8) 

The result is concrete signing of attacker-selected transaction data and reporting or scheduling funds that are not spendable.

### Likelihood Explanation
The attack requires only bytes accepted by `ReceivedOutput::read`: a canonical scalar, a consensus-valid `TxOut`, and an arbitrary `OutPoint`. No private key, malformed curve point, hash collision, or control of an honest validator is needed.

The attacker must get an integrator to consume the serialized output rather than relying exclusively on `Scanner`; the scanner itself constructs `ReceivedOutput` from actual transaction outputs. [5](#0-4) 

For the normal wallet key, the correct spendable `TxOut` is public because it is simply the P2TR script for the group key. [10](#0-9) 

### Recommendation
Do not treat `ReceivedOutput::read` as restoring trusted scanned state without provenance. Either:

- make the type impossible to construct except through `Scanner`, removing public deserialization for trusted spending paths; or
- cryptographically/authenticatedly persist scanned outputs and verify their provenance after deserialization; or
- before creating `SignableTransaction`, independently fetch the referenced outpoint from a trusted Bitcoin view and require exact equality of `TxOut`, transaction confirmation, and spendability.

At minimum, document that `ReceivedOutput::read` returns an unauthenticated claim and must not drive signing without UTXO verification.

### Proof of Concept
Conceptually, for a Serai wallet with group key `K`:

1. Derive the ordinary spend script:

```rust
let script = p2tr_script_buf(K).unwrap();
```

2. Create a fake output referencing a nonexistent transaction:

```rust
let fake = ReceivedOutput {
  offset: Scalar::ZERO,
  output: TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: script,
  },
  outpoint: OutPoint {
    txid: arbitrary_txid,
    vout: 0,
  },
};
```

The same object can alternatively be produced as serialized bytes and accepted by `ReceivedOutput::read`, since the reader only parses the three fields. [4](#0-3) 

3. Pass it to `SignableTransaction::new`; the fake value contributes to `input_sat` and the fake outpoint becomes a transaction input. [6](#0-5) 

4. Calling `multisig` succeeds for the normal group-key script because only the prevout script is checked against the offset-adjusted key; the outpoint's existence is not checked. [8](#0-7) 

5. `sign` produces shares over `taproot_key_spend_signature_hash` using `Prevouts::All`, causing participants to sign a transaction that spends a nonexistent UTXO. [7](#0-6)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L77-86)
```rust
/// Return the Taproot address payload for a public key.
///
/// If the key is odd, this will return None.
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-221)
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
```
