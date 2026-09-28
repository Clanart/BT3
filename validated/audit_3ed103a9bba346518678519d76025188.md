### Title
`ReceivedOutput::read` trusts an attacker-supplied scalar offset without checking it actually derives the output's script key, letting untrusted bytes report received funds that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` bundles a `Scalar` offset, a `TxOut`, and an `OutPoint`. `ReceivedOutput::read` deserializes all three fields verbatim and performs no consistency check that the serialized `offset` is the discrete-log tweak that maps the wallet's group key to the `script_pubkey` inside the serialized `TxOut`. The only place the offset/script relation is ever established is `Scanner::scan_transaction`, which looks up the offset in `self.scripts` keyed by `script_pubkey` — that invariant is created in memory and is not re-verified on deserialization. [1](#0-0) [2](#0-1) 

### Finding Description
The bug class is unauthenticated input selecting a resource outside its intended scope (path traversal). The analog in bitcoin-serai is an attacker-controlled *identifier* — the serialized `offset` — selecting which private tweak will be used to spend an output, with no binding between that identifier and the output's actual locking script:

- `ReceivedOutput::read` reads `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`. Any `(offset, output, outpoint)` triple is accepted. [3](#0-2) 
- The spending key for an output is derived as `script_key - offset·G`, i.e., the offset is assumed to be the tweak registered for that exact `script_pubkey` (see `Output::key` usage and `Scanner::register_offset`, which is what legitimately establishes `script_pubkey ↔ offset`). [4](#0-3) [5](#0-4) 
- `register_offset` documents that arbitrary offsets "may introduce a script path into the output, allowing the output to be spent by satisfaction of an arbitrary script" — yet `read` accepts arbitrary offsets with no such provenance. [6](#0-5) 

So a crafted byte stream fed to `ReceivedOutput::read` (e.g., an outpoint paying to an unrelated key, paired with any offset) is accepted as a `ReceivedOutput` claiming `value()` sats received. When the wallet later attempts to spend it, `key()`/the offset tweak resolves to a private key that does not control the real `script_pubkey`, so the "received" funds are unspendable — or, worse, the output's actual script is a key- or script-path spend belonging to the attacker.

### Impact Explanation
Funds reported received that are not spendable: an accounting/crediting layer that ingests serialized `ReceivedOutput`s (as `processor/src/networks/bitcoin.rs` `Output::read` does via `ReceivedOutput::read`) will treat attacker-crafted bytes as a balance-bearing, spendable output. The UTXO may not exist, may belong to a third party, or may carry a tapscript path the attacker controls — producing false credit and either failed spends or signing sessions over a plan referencing an output the multisig cannot actually claim. [7](#0-6) 

### Likelihood Explanation
Reachable wherever serialized `ReceivedOutput` bytes cross a trust boundary (network-supplied wallet data, imported/backed-up output lists, or any peer-provided encoding). Serialization round-trip tests only exercise honest data, so the missing check is unexercised. [8](#0-7)  Severity Medium: integrity impact (false crediting of unspendable funds) but requires a consumer that trusts deserialized outputs rather than freshly scanned ones.

### Recommendation
Bind the offset to the output at deserialization: either (a) store/derive the group key context and verify `output.script_pubkey == p2tr_script_buf(key + offset·G)` inside `ReceivedOutput::read` (requiring the key as a parameter), or (b) remove `offset` from the wire format entirely and re-derive it via `Scanner`-style script lookup on load, so a `ReceivedOutput` can only ever exist with a proven script↔offset correspondence. Also reject non-P2TR `TxOut`s and offsets whose tweaked key has odd Y in `read`. [9](#0-8) 

### Proof of Concept
```rust
// Craft a ReceivedOutput claiming funds at an outpoint we do not control.
let attacker_offset = Scalar::random(&mut OsRng);          // arbitrary
let victim_txout = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(
    TweakedPublicKey::dangerous_assume_tweaked(x_only(&attacker_keypoint))
  ),                                                      // script NOT equal to wallet key + offset*G
};
let fake_outpoint = OutPoint::new(Txid::all_zeros(), 0);

let mut bytes = vec![];
bytes.extend(attacker_offset.to_bytes());
bytes.extend(serialize(&victim_txout));
bytes.extend(serialize(&fake_outpoint));

// Accepted without complaint:
let got = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(got.value(), 100_000);   // reported balance
// got.offset() (= attacker_offset) does not spend victim_txout's actual script:
// the derived private tweak yields a key unrelated to the real lock,
// so any spend built from `got` is invalid / credits unspendable funds.
```
The same construction with a `script_pubkey` containing an attacker-controlled tapscript leaf (possible because `read` does not enforce key-path-only P2TR derived from `key + offset·G`) yields an output spendable by the attacker, not the multisig.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L99-134)
```rust
impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

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

**File:** networks/bitcoin/src/wallet/mod.rs (L168-179)
```rust
  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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

**File:** networks/bitcoin/tests/wallet.rs (L72-75)
```rust
  assert_eq!(
    ReceivedOutput::read::<&[u8]>(&mut outputs[0].serialize().as_ref()).unwrap(),
    outputs[0]
  );
```
