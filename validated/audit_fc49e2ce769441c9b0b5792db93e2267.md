### Title
Deserialized `ReceivedOutput` trusts an unverified offset/script pairing, letting unspendable outputs be claimed as spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` enforces the binding between a registered scalar offset and the expected P2TR `script_pubkey` (it only yields outputs whose script was derived as `key + offset*G`). `ReceivedOutput::read`, however, deserializes `offset`, `output`, and `outpoint` independently and never re-checks that `output.script_pubkey == p2tr_script_buf(key + offset*G)`. This mirrors the Blueberry bug: state established under one validation path (scan) is later trusted on a different path (deserialization) without re-validating the invariant.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`:

- `Scanner::register_offset`/`scan_transaction` maintain a `scripts: HashMap<ScriptBuf, Scalar>` map so every emitted `ReceivedOutput` carries an offset that provably produces the output's script [1](#0-0) .
- `ReceivedOutput::read` reads a scalar via `Secp256k1::read_F` and consensus-decodes the `TxOut`/`OutPoint` with no consistency check [2](#0-1) .
- Consumers treat the deserialized struct as authoritative. `Output::key()` in `processor/src/networks/bitcoin.rs` derives the owning key as `scriptkey - offset*G`, requiring only that the script be P2TR — so any attacker-chosen P2TR script plus any offset yields a coherent-looking output [3](#0-2) . `Output::read` pipes untrusted bytes straight into `ReceivedOutput::read` [4](#0-3) .

### Impact Explanation
An attacker supplying crafted bytes to `ReceivedOutput::read` (or `Output::read`) can report an arbitrary on-chain P2TR output — e.g., one paying a third party — paired with an offset under their control, as funds received by the target key. Two failure modes follow:

- Funds reported as received are not spendable: the script does not equal `key + offset*G`, so the declared offset does not unlock the real output.
- The spending path signs for the derived key `scriptkey - offset*G`; for an attacker-chosen script/offset pair this can make the multisig spend inputs under an attacker-influenced effective key relation, or accept inputs the wallet cannot actually spend, corrupting balance accounting.

### Likelihood Explanation
Anywhere serialized `ReceivedOutput`s cross a trust boundary (network messages, DB, RPC-fed scanners), an unprivileged party controls the bytes. No threshold cooperation, valid proof, or leaked secret is needed — only the ability to feed crafted bytes to `read`.

### Recommendation
Either make `ReceivedOutput` opaque to unverified sources, or re-validate in `read`/at consumption time: given the known base `key`, assert `output.script_pubkey == p2tr_script_buf(key + GENERATOR*offset)`. Alternatively, store/serialize only the outpoint and re-derive the offset via `Scanner` lookup, re-establishing the script↔offset invariant the scanner enforced.

### Proof of Concept
```rust
// Conceptual: craft a ReceivedOutput claiming a victim's P2TR output
// with an offset that does not correspond to it.
let mut bytes = Vec::new();
bytes.extend(attacker_chosen_offset.to_bytes());          // arbitrary Scalar
bytes.extend(serialize(&victim_txout));                   // any P2TR TxOut
bytes.extend(serialize(&victim_outpoint));
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted
// forged.offset() / forged.output() are trusted downstream even though
// victim_txout.script_pubkey != p2tr_script_buf(key + G*offset)
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-214)
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
    res
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

**File:** processor/src/networks/bitcoin.rs (L145-165)
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
```
