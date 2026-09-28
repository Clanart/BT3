### Title
`ReceivedOutput::read` trusts an attacker-controlled scalar offset that is never checked against the output's `script_pubkey`, letting metadata re-attribute a deposit to a key that cannot spend it - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class in the advisory — a trusted selection/registration being redirected to payload content that was never validated — maps onto `ReceivedOutput` in `bitcoin-serai`. `ReceivedOutput` is a self-describing record: `offset` (metadata claiming which HDKD-derived key owns the coin) plus the `TxOut`/`OutPoint` payload. `Scanner::register_offset`/`scan_transaction` build this binding correctly by construction (`scripts: HashMap<ScriptBuf, Scalar>` keyed on the exact `p2tr_script_buf(key + G*offset)`), but `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` as three independent fields and performs no check that `p2tr_script_buf(group_key + G*offset) == output.script_pubkey`. The invariant that ties the "scanned" payload (the script actually paid on-chain) to the claimed key is only enforced on the scan path, not the read path. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput::read` reads `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns them as a coherent output with no consistency check (mod.rs:122-134). Downstream, `Output::key()` re-derives the owning key purely from this metadata: it extracts the x-only key embedded in `output.script_pubkey` and subtracts `G * offset`, trusting the serialized scalar to be the one `Scanner` registered (processor/src/networks/bitcoin.rs:112-122). `SignableTransaction::new` copies each input's `offset` into `self.offsets` (send.rs:176) and `multisig` re-keys the threshold keys with `keys.clone().offset(self.offsets[i])` before checking `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (send.rs:276-281). [3](#0-2) [4](#0-3) 

So an unprivileged party that feeds crafted bytes to `ReceivedOutput::read`/`Output::read` can attach an arbitrary offset to a real deposit `script_pubkey`. The `key()`/balance path then reports the output under a group key that was never the one scanned for — metadata pointing at an "unscanned" (unvalidated) payload — and the output may additionally be attributed to a different multisig instance or indexed under the wrong key, while being effectively unspendable.

### Impact Explanation
- **Funds reported received that are not spendable**: with a mismatched offset, `Output::key()` returns a wrong group key (bitcoin.rs:120-122), so the output is accounted under a key that cannot authorize it. If it nevertheless reaches `SignableTransaction::multisig`, the `p2tr_script_buf(offset.group_key()) != script_pubkey` check returns `None` (send.rs:277-279), aborting signing — a permanent freeze of a real deposit rather than theft.
- **Cross-key misattribution**: because `offset` is an arbitrary scalar, a serialized output for multisig A's script can be framed with an offset that makes `key()` report multisig B's key, corrupting key→output accounting used by the scanner event path.
- No signature forgery or key recovery: the only consistency check (`multisig`, send.rs:277) fails closed, so the ceiling is unspendable/misattributed funds — Medium.

### Likelihood Explanation
Requires attacker-controlled bytes reaching `ReceivedOutput::read`/`Output::read` (explicitly in scope as an untrusted-bytes sink). Where Serai components round-trip `ReceivedOutput`/`Output` through serialization (e.g., `Output::read` at processor/src/networks/bitcoin.rs:145-166, which also reads unchecked `kind`, `presumed_origin`, and `data` metadata), a relayed or tampered record suffices; no validator key, collusion, or RPC compromise is needed. Confidence is limited by reachability: the coordinator's own scanner constructs `ReceivedOutput` in memory and never re-reads it, so exploitation depends on a deserialized output being trusted across a component/storage boundary.

### Recommendation
Make the binding self-verifying on read: either (a) have `ReceivedOutput::read` take the expected `ProjectivePoint`/registered script set and assert `p2tr_script_buf(key + G*offset) == output.script_pubkey`, or (b) store only the `OutPoint`/registration index and re-derive `offset` from the `Scanner`'s `scripts` map at use time, so the offset can never diverge from what was scanned. At minimum, `Output::key()` should recompute the script from `(group_key, offset)` and compare against `output.script_pubkey` before returning.

### Proof of Concept
```rust
// networks/bitcoin: conceptual
let mut scanner = Scanner::new(group_key).unwrap();
let real = scanner.register_offset(o).unwrap();          // script S = p2tr(key + G*o)
let honest = scanner.scan_transaction(&deposit_tx)[0];    // deposit to S

// Attacker crafts bytes: same TxOut/OutPoint, but offset o' = o + delta
let mut buf = honest.serialize();
// overwrite first 32 bytes (the offset field) with o'
buf[..32].copy_from_slice(&(o + delta).to_bytes());
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted, no check

// Output::key() now reports group_key - G*delta instead of group_key
// SignableTransaction::multisig returns None -> deposit frozen, or
// the output is attributed to the wrong key in scanner accounting.
```

Verification status: the missing offset↔script consistency check is confirmed in `ReceivedOutput::read` (mod.rs:122-134) and the blind offset subtraction in `Output::key()` (bitcoin.rs:120-122). Not fully verified: the exact production path where attacker bytes reach `ReceivedOutput::read` inside the coordinator/processor rather than in-memory scanner results; if no such path exists in production usage, this reduces to hardening rather than an exploitable finding.

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```
