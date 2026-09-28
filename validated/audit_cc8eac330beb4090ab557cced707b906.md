### Title
`ReceivedOutput::read` accepts an offset/script_pubkey pair never registered with the Scanner, yielding outputs reported as received but not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes value being delivered to a target that was never checked against the authorized recipient set (`set_debt_ceiling` minting to an address that is neither a Controller nor a PegKeeper). The analogous shape in Serai is the Bitcoin `Scanner`'s registered-output set: `Scanner::register_offset` builds the authoritative mapping `script_pubkey -> offset` and `scan_transaction` only emits `ReceivedOutput`s whose `script_pubkey` is in that map, so every scanned output carries an offset actually bound to the paying script. `ReceivedOutput::read` deserializes `(offset, TxOut, OutPoint)` from untrusted bytes with no check that the `offset` corresponds to `output.script_pubkey` under the wallet key — i.e., it accepts "recipients" (offset/script bindings) that were never registered. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` where each `script_pubkey` is `p2tr_script_buf(key + GENERATOR * offset)` for a registered `offset`. `scan_transaction` only produces a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` hits, and copies the registered offset into it — guaranteeing `offset` is the correct re-keying scalar for that output.

`ReceivedOutput::read` performs `Secp256k1::read_F` for the offset and `consensus_decode` for the `TxOut`/`OutPoint`, then returns the struct with zero verification that `p2tr_script_buf(wallet_key + GENERATOR * offset) == output.script_pubkey` (and it cannot even check the outpoint exists). The binding that `scan_transaction` enforces — "this output is among the registered offsets" — is completely dropped on deserialization. Downstream spending code (`SignableTransaction` / per-input re-keying in `wallet/`) trusts `output.offset()` to transform the multisig key for that input's sighash; a mismatched offset produces signatures for a key that does not control the UTXO.

Contrast with sibling types: `Participant` re-validates on `BorshDeserialize` (`Participant::new(...)` in `crypto/dkg/src/lib.rs:121-126`), `ThresholdKeys::read` re-runs `ThresholdKeys::new`'s full validation including participant-set membership (`crypto/dkg/src/lib.rs:355-365`, `625-631`), and `ThresholdKeys::view` rejects any `included` element `> n` or duplicated (`crypto/dkg/src/lib.rs:486-491`). `ReceivedOutput` is the one deserializable wallet artifact that skips re-validating its core membership binding. [4](#0-3) [5](#0-4) 

### Impact Explanation
A `ReceivedOutput` supplied over an untrusted channel (a peer relaying wallet state, an imported output list, a coordinator-supplied input set) can name a real on-chain `OutPoint`/`TxOut` paying to a registered script while carrying a different offset (e.g., `Scalar::ZERO` for an offset-keyed deposit). The wallet treats the funds as received and spendable, but every signing attempt derives the wrong spending key: the produced signature commits to `key + offset'*G ≠` the output key, so the transaction is unspendable/invalid. This realizes the accepted impact "funds reported received that are not spendable" and can lock wallet inputs (the bad output remains queued as a spendable UTXO). Severity: Medium — it requires an attacker to feed crafted bytes to a `ReceivedOutput::read` consumer rather than forge on-chain data.

### Likelihood Explanation
Moderate. Honest scanners only ever produce consistent pairs, so the bug is latent until a deployment ingests `ReceivedOutput` bytes from anything other than its own `scan_transaction` results — a documented serialization format (`write`/`serialize`/`read` is a public round-trip API, and the sink is explicitly attacker-feedable in scope). The offset is additionally surjective rather than bijective (`register_offset` increments until an even point), widening the space of plausible-but-wrong offsets.

### Recommendation
Either make `ReceivedOutput` unforgeable-in-practice by carrying/proving the binding, or require re-validation at consumption:

1. Give `ReceivedOutput::read` a variant taking the wallet `ProjectivePoint` (or the `Scanner`) and reject unless `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey.clone())` and the script is currently registered.
2. Alternatively, serialize the output keyed by its `script_pubkey` and resolve the offset via `Scanner::scripts` at load time — never trusting a serialized offset.
3. Document that deserialized `ReceivedOutput`s must not be mixed into spend planning without re-anchoring to the Scanner's registration table.

### Proof of Concept
```rust
// Wallet key K (even), offset o registered => script S = p2tr(K + o*G)
let mut scanner = Scanner::new(key).unwrap();
let offset = scanner.register_offset(Scalar::random(&mut OsRng)).unwrap();
let script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * offset)).unwrap();

// Attacker crafts bytes: real TxOut paying to `script`, but offset' = 0
let forged = {
    let mut buf = vec![];
    buf.extend(Scalar::ZERO.to_repr().as_ref());            // wrong offset
    buf.extend(serialize(&TxOut {
        value: Amount::from_sat(100_000),
        script_pubkey: script.clone(),
    }));
    buf.extend(serialize(&OutPoint::new(real_txid, 0)));
    buf
};

// Accepted without error; binding never re-checked
let out = ReceivedOutput::read::<&[u8]>(&mut forged.as_slice()).unwrap();
assert_eq!(out.offset(), Scalar::ZERO);          // claims base-key offset
assert_eq!(out.output().script_pubkey, script);  // but script needs `offset`

// SignableTransaction::new(vec![out], ...) re-keys the input with offset 0:
// the multisig signs for p2tr(K) while the UTXO pays p2tr(K + o*G),
// so the input can never be validly spent — reported received, unspendable.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
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
  }
```

**File:** crypto/dkg/src/lib.rs (L121-126)
```rust
impl borsh::BorshDeserialize for Participant {
  fn deserialize_reader<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Participant::new(u16::deserialize_reader(reader)?)
      .ok_or_else(|| io::Error::other("invalid participant"))
  }
}
```

**File:** crypto/dkg/src/lib.rs (L361-365)
```rust
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }
```
