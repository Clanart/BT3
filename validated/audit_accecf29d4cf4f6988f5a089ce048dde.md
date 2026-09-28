### Title
ReceivedOutput deserialization never binds `offset` to `output.script_pubkey`, allowing a forged output that resolves to an unspendable key - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The bug class in the report is an indirection resolved differently at check time than at use time (a symlink pointing outside the authorized object). The Serai analog is `ReceivedOutput`: it carries an `offset` scalar (the "handle" used to derive the signing key) separately from the `TxOut`/`OutPoint` (the object actually spent). `ReceivedOutput::read` accepts the two fields independently and never verifies that `script_pubkey` equals `p2tr_script_buf(key + offset*G)`, so untrusted bytes can pair any script with any offset — the same "check one name, act on the resolved target" divergence as symlink following.

### Finding Description
`Scanner::scan_transaction` only ever produces `ReceivedOutput`s where the script was derived from `key + offset*G`, so `offset`/`script` consistency is an implicit invariant [1](#0-0) . But `ReceivedOutput::read` reconstructs the struct from the wire with no such check: it reads an arbitrary scalar, an arbitrary `TxOut`, and an arbitrary `OutPoint`, then returns them as a unit [2](#0-1) . The offset is then used as the additive key tweak for signing — the same mechanism documented as "the scalar offset to obtain the key usable to spend this output" [3](#0-2)  — and applied to the threshold keys via `ThresholdKeys::offset`/`ThresholdView` [4](#0-3) [5](#0-4) . The downstream key recovery is equally trusting: `Output::key()` reconstructs the spend key as `script_key - offset*G`, again assuming the pairing is honest [6](#0-5) .

### Impact Explanation
An unprivileged party who can feed crafted bytes into `ReceivedOutput::read` (or `Output::read`, which wraps it [7](#0-6) ) can declare an output that the scanner would never produce: a real, well-formed prevout paired with an offset that does not correspond to its `script_pubkey`. The wallet then builds and FROST-signs a transaction under key `K + offset*G` while the prevout is locked to `K` (or vice versa): the signature is valid for the wrong key, the transaction is consensus-invalid at the input, and the reported funds are not spendable — matching the "funds reported received that are not spendable" acceptance criterion. Alternatively, pairing an attacker-chosen script with an honest-looking offset mis-attributes an output's kind/origin (the `kinds[offset]` classification downstream is keyed purely on the offset [8](#0-7) ).

### Likelihood Explanation
Requires an attacker-controlled `ReceivedOutput`/`Output` byte stream to reach a wallet/signing path rather than scanner-produced values; that is a real but narrower exposure than a purely remote trigger, and the primary consequence is a bricked spend rather than key extraction. Medium severity.

### Recommendation
In `ReceivedOutput::read` (or in a checked constructor it delegates to), verify consistency: reconstruct the expected script from a base key plus `offset` and reject bytes where `output.script_pubkey` doesn't match, or make the fields private and force all construction through `scan_transaction`, which establishes the invariant. Downstream consumers such as `Output::key` should not need to trust the pairing.

### Proof of Concept
```rust
// Conceptual: an attacker supplies bytes to ReceivedOutput::read
let key = scanner_key(); // honest even-Y group key
let honest_script = p2tr_script_buf(key).unwrap();

let forged = {
    let mut buf = vec![];
    // Claim offset = 5 while the script belongs to offset 0
    buf.extend(Scalar::from(5u64).to_bytes());
    buf.extend(serialize(&TxOut {
        value: Amount::from_sat(100_000),
        script_pubkey: honest_script, // locked to `key`, not key + 5*G
    }));
    buf.extend(serialize(&real_outpoint));
    buf
};

let out = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
// `out` is accepted: offset=5, script=spendable by `key`
// Signing path tweaks keys by +5 and produces a signature for key + 5*G,
// which cannot satisfy the prevout's script => transaction invalid,
// funds reported received are unspendable.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
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

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```

**File:** crypto/dkg/src/lib.rs (L414-417)
```rust
  pub fn offset(mut self, offset: C::F) -> ThresholdKeys<C> {
    self.offset += offset;
    self
  }
```

**File:** crypto/dkg/src/lib.rs (L518-521)
```rust
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
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

**File:** processor/src/networks/bitcoin.rs (L693-699)
```rust
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
```
