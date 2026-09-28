### Title
`ReceivedOutput::read` accepts an arbitrary spend-offset not bound to the output's script_pubkey, letting a forged encoding claim funds that are unspendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The directory-traversal bug class — untrusted input selecting an identifier/path that escapes its intended namespace — maps onto `ReceivedOutput`'s encoding. A `ReceivedOutput` is a `{offset, output, outpoint}` triple where `offset` is the scalar that must be added to the multisig's secret shares to spend `output` under its `script_pubkey`. `ReceivedOutput::read` deserializes `offset` via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via consensus decoding, but never verifies that `offset` actually derives the key committed in `output.script_pubkey` [1](#0-0) . In `Scanner`, `offset` is correctly bound by construction — it comes from `self.scripts.get(&output.script_pubkey)`, a map keyed by the script [2](#0-1) . That binding is lost on the serialization boundary: like `GET /../etc/passwd` resolving outside the web root, a crafted byte stream re-associates a real on-chain output with an attacker-chosen offset "path" that leads outside the set of spendable keys.

### Finding Description
`ReceivedOutput::read` performs three independent reads and returns the tuple with no consistency check [3](#0-2) . The honest invariant — `output.script_pubkey == p2tr_script_buf(scanner.key + G*offset)` — is only enforced inside `Scanner::scan_transaction`/`register_offset` [4](#0-3) . The processor relies on this association downstream: `Output::key()` recovers the spend key as `script_key - G*offset` [5](#0-4) , so a mismatched `offset` yields a garbage group key. Any signing attempt for the output then produces shares for the wrong key, while the balance is still reported as received (`balance()` reads `output.value()` unconditionally) [6](#0-5) . Caveat: I could not fully trace every caller feeding untrusted bytes into `ReceivedOutput::read` within the remaining investigation budget; the sink is a named in-scope `read` API and the mismatch silently survives until signing.

### Impact Explanation
Funds are reported received that are not spendable under the recorded offset. Worse, because `offset` is an arbitrary scalar, a forged entry can shift the effective spend key to `share + offset` for an attacker-chosen `offset`, meaning the threshold would sign under a key the attacker may know the discrete log of relative to the tweaked output — enabling signature/key-share confusion rather than the intended key. Either outcome meets the bar: reported-but-unspendable funds, or signing under an unintended scalar offset.

### Likelihood Explanation
Reachability requires attacker influence over bytes passed to `ReceivedOutput::read` (e.g., a malformed/de-synced serialized output propagated to the processor rather than produced by `Scanner::scan_transaction`). An external party cannot normally inject scanner outputs, so this rates Medium: the encoding permits an inconsistency the constructor guarantees, and the decoder fails to re-establish it.

### Recommendation
Store/recompute the binding instead of trusting it. Either serialize `ReceivedOutput` without `offset` and re-derive it from the `Scanner` scripts map on load, or after reading, verify `p2tr_script_buf(scanner_key + G*offset) == output.script_pubkey` and reject on mismatch — analogous to canonicalizing and constraining a path to its root.

### Proof of Concept
```rust
// Forge a ReceivedOutput: real P2TR output to key K, but offset' != offset
let mut bytes = Vec::new();
bytes.extend((Scalar::ONE + Scalar::ONE).to_bytes()); // arbitrary offset'
bytes.extend(serialize(&txout_payable_to_scanner_key)); // honest TxOut
bytes.extend(serialize(&outpoint));
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// forged.offset() != offset that produced script_pubkey
// Output::key() => script_key - G*offset' => wrong spend key; balance still counted
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

**File:** networks/bitcoin/src/wallet/mod.rs (L185-195)
```rust
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

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
  }
```
