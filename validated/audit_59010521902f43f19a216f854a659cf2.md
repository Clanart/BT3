### Title
Deserialized Bitcoin outputs can claim spendable funds without proving the referenced UTXO exists - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an attacker-controlled `(offset, TxOut, OutPoint)` tuple without authenticating that the outpoint exists on-chain, contains the supplied `TxOut`, or was produced by `Scanner`. The processor later trusts the embedded value as a spendable balance and uses the embedded outpoint as transaction input. A forged serialized output can therefore cause signers to create and sign a transaction spending a nonexistent UTXO.

### Finding Description
`ReceivedOutput::read` only parses a scalar offset, a consensus-encoded `TxOut`, and a consensus-encoded `OutPoint`; it does not cryptographically bind the `TxOut` to the referenced outpoint or prove that the outpoint is a confirmed UTXO. [1](#0-0)  The processor's `Output::read` directly wraps this unchecked value. [2](#0-1)  Its reported balance is then taken directly from the attacker-controlled `TxOut.value`. [3](#0-2) 

During transaction creation, `SignableTransaction::new` trusts `input.outpoint` when constructing `TxIn` values and trusts `input.output.value` when determining available funds. [4](#0-3)  The later `multisig` check only verifies that the declared offset makes the threshold key produce the supplied `script_pubkey`; it does not verify that the referenced outpoint exists or contains that output. [5](#0-4)  Signing commits to these forged prevouts through `Prevouts::All`, and completion inserts the resulting Schnorr signatures into the forged transaction. [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` bytes can claim an arbitrary balance behind a multisig-controlled Taproot script while naming a fake or unrelated outpoint. The scheduler can treat those bytes as spendable funds, and the signing flow can produce a fully signed Bitcoin transaction that consensus rejects because its referenced input is absent, already spent, or has different contents. This meets both the unintended-signing and “funds reported received that are not spendable” impacts, and it can strand or mis-account planned payments that depend on the fabricated balance.

### Likelihood Explanation
The required input is only a syntactically valid serialization; no validator key, node compromise, malformed curve point, or consensus violation is needed. The attacker must choose an offset and `script_pubkey` pair that passes the existing key-ownership check, which is straightforward for the public base key because `offset = 0` targets the ordinary key path, or a registered/publicly known offset can be reused. The missing check is deterministic and reachable whenever untrusted `ReceivedOutput` bytes are deserialized rather than obtained directly from `Scanner::scan_transaction` or `Scanner::scan_block`. [8](#0-7) 

### Recommendation
Do not treat deserialized `ReceivedOutput` values as proof of spendability. Add an authenticated/verified constructor or validation step that resolves `outpoint` against the Bitcoin chain and requires the returned `TxOut` to equal the serialized `TxOut` exactly, including value and script. Also verify that the output is unspent, sufficiently confirmed, and not an immature coinbase before placing it in the scheduler's UTXO set. If serialized outputs are intended only for trusted local persistence, make that trust boundary explicit and use a separate unverified wire type so public input cannot enter through `ReceivedOutput::read`.

### Proof of Concept
1. Obtain the target threshold key's ordinary P2TR script using `p2tr_script_buf(group_key)`.
2. Serialize this forged record in the format accepted by `ReceivedOutput::read`:
   - `offset`: canonical encoding of `Scalar::ZERO`
   - `TxOut`: a large attacker-chosen `value` and the target's `script_pubkey`
   - `OutPoint`: a random/nonexistent txid and vout
3. Feed those bytes to `ReceivedOutput::read`.
4. Wrap the result in the processor `Output` encoding or pass it to `SignableTransaction::new`.
5. The constructor counts the forged value as available input and inserts the fake outpoint into `TxIn.previous_output`. [4](#0-3) 
6. `multisig` accepts the input because the script matches `group_key + 0*G`. [5](#0-4) 
7. Normal preprocessing, signing, and completion produce witness signatures for a transaction whose input cannot be spent on-chain. [9](#0-8)

### Citations

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

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
  }
```

**File:** processor/src/networks/bitcoin.rs (L145-166)
```rust
  fn read<R: io::Read>(mut reader: &mut R) -> io::Result<Self> {
    Ok(Output {
      kind: OutputType::read(reader)?,
      presumed_origin: {
        let mut io_reader = scale::IoReader(reader);
        let res = Option::<Vec<u8>>::decode(&mut io_reader)
          .unwrap()
          .map(|address| Address::try_from(address).unwrap());
        reader = io_reader.0;
        res
      },
      output: ReceivedOutput::read(reader)?,
      data: {
        let mut data_len = [0; 2];
        reader.read_exact(&mut data_len)?;

        let mut data = vec![0; usize::from(u16::from_le_bytes(data_len))];
        reader.read_exact(&mut data)?;
        data
      },
    })
  }
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L373-428)
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
}

pub struct TransactionSignatureMachine {
  tx: Transaction,
  sigs: Vec<AlgorithmSignatureMachine<Secp256k1, Schnorr>>,
}

impl SignatureMachine<Transaction> for TransactionSignatureMachine {
  type SignatureShare = Vec<SignatureShare<Secp256k1>>;

  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }

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
