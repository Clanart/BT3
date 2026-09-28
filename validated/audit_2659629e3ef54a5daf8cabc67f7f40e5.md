### Title
`ReceivedOutput` accepts an arbitrary `offset`/`script_pubkey` pair with no consistency check, letting mismatched bytes report unspendable funds as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a wallet initialized with a set of mutually inconsistent contract addresses, producing a wallet that appears valid but cannot operate correctly, locking funds. The Serai analog is `ReceivedOutput` (the wallet's "this output is ours and spendable" record): its `read` deserializer accepts an `offset`, a `TxOut` (containing the `script_pubkey`), and an `OutPoint` as three independent fields with no check that `offset` actually derives `script_pubkey` from the scanner's key.

### Finding Description
`Scanner::register_offset`/`scan_transaction` guarantee that every emitted `ReceivedOutput` satisfies `output.script_pubkey == p2tr(key + G*offset)` [1](#0-0) . However `ReceivedOutput::read` parses `offset`, `output`, and `outpoint` independently and returns them verbatim, with no check that the offset reproduces the script [2](#0-1) . The same unchecked trust propagates into the processor: `Output::read` calls `ReceivedOutput::read` directly [3](#0-2) , and `Output::key()` reconstructs the owning key by subtracting `G * offset` from the key embedded in the claimed script [4](#0-3) . When a `SignableTransaction` is built, each input's `offset` is applied via `ThresholdKeys::offset`, shifting the signer's share so the produced signature is only valid for `key + G*offset` [5](#0-4) .

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` (explicitly in-scope untrusted input) supplies a `TxOut` paying to a real Serai scanner script but pairs it with an arbitrary wrong `offset` (or vice versa). The processor then records a received output attributed to key `K = scriptKey - G*offset` [4](#0-3)  and, when spending, applies `offset` to the threshold keys. The signature is produced for `K + G*offset = scriptKey`, but if `offset` doesn't match the actual tweak used to build the script, the tweaked group key cannot sign for it — the output is recorded as received yet is unspendable, permanently locking funds under the multisig. This is exactly the report's "components not synchronized ⇒ wallet works incorrectly ⇒ lock of funds" class, mapped onto the wallet's spendable-output record.

### Likelihood Explanation
Reachable only where serialized `ReceivedOutput`/`Output` bytes cross a trust boundary (e.g., round-tripped through coordinator messages or restored state influenced by an external party). `read` performs no rejection path for inconsistency, so any delivered mismatch is silently accepted; the failure only surfaces later as unspendable funds. Medium likelihood, High impact → Medium/High overall.

### Recommendation
Either make `ReceivedOutput` construction private to `Scanner` (removing public `read`), or add a `verify(key)`/`new` that recomputes `p2tr_script_buf(key + G*offset)` and rejects inputs whose `output.script_pubkey` does not match, before the output is registered or spent. At minimum, `Output::key()` should validate consistency rather than silently deriving a key from unchecked fields.

### Proof of Concept
```rust
// Attacker supplies serialized bytes for a ReceivedOutput
let mut bytes = vec![];
bytes.extend(Scalar::from(7u64).to_bytes());          // arbitrary offset
bytes.extend(serialize(&TxOut {                        // any valid P2TR output
    value: Amount::from_sat(100_000),
    script_pubkey: p2tr_script_buf(real_serai_key).unwrap(),
}));
bytes.extend(serialize(&OutPoint::new(txid, 0)));

let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted

// Processor side:
let attributed_key = script_key_of(&ro) - (ProjectivePoint::GENERATOR * ro.offset());
// attributed_key != real_serai_key when offset mismatches the script;
// spending applies ro.offset() to ThresholdKeys, so the multisig signs
// under the wrong tweaked key and the output is locked forever.
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-212)
```rust
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
```

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
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

**File:** crypto/dkg/src/lib.rs (L409-417)
```rust
  /// Offset the keys by a given scalar to allow for various account and privacy schemes.
  ///
  /// This offset is ephemeral and will not be included when these keys are serialized. The
  /// offset is applied on top of any already-existing scalar/offset.
  #[must_use]
  pub fn offset(mut self, offset: C::F) -> ThresholdKeys<C> {
    self.offset += offset;
    self
  }
```
