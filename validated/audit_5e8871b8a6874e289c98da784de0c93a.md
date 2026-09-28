### Title
`ReceivedOutput::read` trusts an untrusted offset/script_pubkey pairing, enabling "funds received" that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes stored, attacker-controlled data being accepted and later consumed in a privileged context without validation (unsanitized SVG rendered as active content). The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`: it deserializes a spend-offset `Scalar`, a `TxOut`, and an `OutPoint` from raw bytes and reassembles a `ReceivedOutput` — a value that everywhere else is only minted by `Scanner::scan_transaction`/`scan_block` after verifying `output.script_pubkey` is a registered `p2tr_script_buf(key + G*offset)`. The `read` path performs no such consistency check, so arbitrary bytes are "rendered" into a trusted wallet object claiming spendable funds.

### Finding Description
`Scanner::new` and `Scanner::register_offset` populate `scripts: HashMap<ScriptBuf, Scalar>` mapping `p2tr_script_buf(key + GENERATOR * offset)` → `offset` [1](#0-0) . `scan_transaction` only produces a `ReceivedOutput` when `output.script_pubkey` hits that map, so the invariant "`output` pays to `key + offset*G`" holds [2](#0-1) . However, `ReceivedOutput::read` decodes `offset`, `output`, and `outpoint` straight from the byte stream with zero checks on the script or the offset relationship [3](#0-2) . Downstream code treats a `ReceivedOutput` as spendable: `SignableTransaction::new` consumes `Vec<ReceivedOutput>` and later signs per-input with `key + GENERATOR * output.offset()` semantics (tweaked per the offset), and `Output::key()` in the processor subtracts `GENERATOR * offset` from the script's x-only key, trusting the pairing [4](#0-3) . Like the TypeBot bug where stored bytes were later rendered in a security context that assumed a benign image, here stored/transmitted bytes are reconstituted into a trusted object whose critical invariant (script ↔ offset correspondence) is never re-verified.

### Impact Explanation
Any path that persists `ReceivedOutput` bytes (DB, messages, cache) and later `read`s them gives an unprivileged party who can influence those bytes the ability to fabricate outputs: an offset/scalar and `TxOut` that don't correspond to any registered script. Consumers will then (a) report balance/funds received that are not actually spendable under the multisig key, and (b) construct `SignableTransaction`s over fake prevouts whose signatures are generated for inputs that don't exist or can't be spent — leading to rejected transactions, stranded-fee burns, or accounting of phantom deposits. That matches the accepted impact classes ("funds reported received that are not spendable").

### Likelihood Explanation
Reachability requires attacker-influenced bytes reaching `ReceivedOutput::read` (e.g., a corrupted/hostile peer-fed serialization or tampered persisted record). It needs no key material, no threshold collusion, and no validator misbehavior — only control of the input bytes. The defect is deterministic: the function contains no validation to fail on, so any crafted tuple is accepted whenever the function is invoked.

### Recommendation
Bind the deserialized fields to the invariant the constructor enforces. Either (a) make `ReceivedOutput::read` take the `Scanner`/expected key and verify `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` (and that the offset is the registered canonical one), or (b) store a commitment to the scanner's `scripts` map alongside the output and reject on mismatch. At minimum, document that `read` must only be used on bytes previously produced by `Scanner`, and assert the script is the expected P2TR form.

### Proof of Concept
```rust
// Given a Scanner for `key` (only the ZERO offset registered):
let scanner = Scanner::new(key).unwrap();

// Craft bytes: offset = 1 (never registered), TxOut paying to an
// attacker-controlled P2TR script, any OutPoint.
let mut bytes = Vec::new();
bytes.extend((Scalar::ONE).to_bytes());                     // offset
bytes.extend(serialize(&TxOut {                             // output: arbitrary script
  value: Amount::from_sat(100_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(
    TweakedPublicKey::dangerous_assume_tweaked(attacker_xonly)),
}));
bytes.extend(serialize(&OutPoint::new(txid, 0)));           // outpoint

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// Accepted: forged.offset() == 1, yet scanner never registered offset 1 and
// output.script_pubkey does not equal p2tr_script_buf(key + G*1).
// Any consumer now treats `forged` as a spendable received output.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L162-196)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }

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
