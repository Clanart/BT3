### Title
`ReceivedOutput::read` accepts untrusted bytes without validating the output's `script_pubkey` binds to the claimed key offset - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to a Chainlink consumer trusting `latestRoundData` without checking `roundId`/`answeredInRound`, `ReceivedOutput::read` deserializes `(offset, TxOut, OutPoint)` from attacker-controlled bytes without checking that `output.script_pubkey` is actually the tweaked P2TR script derived from the group key and `offset`. The scanner-side invariant that links `offset ↔ script_pubkey` is only enforced inside `Scanner::scan_transaction`, and is silently dropped on deserialization.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` mapping each registered tweaked P2TR script to its spend offset. `scan_transaction` only produces a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns a matching offset [1](#0-0) . This binding is what makes the `ReceivedOutput` spendable: the offset is added to the key to produce the tweaked key, and the script is the Taproot commitment to that tweaked key.

`ReceivedOutput::read`, however, reads `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns them without any consistency check [2](#0-1) . There is no verification that:

- `output.script_pubkey == p2tr_script_buf(key + G*offset)` for the expected group key, nor
- that the referenced `outpoint`/`TxOut` even pays to the key at all.

Like the missing `answeredInRound >= roundId` check, the deserializer trusts fields that purport to correspond to one another but carries no proof of that correspondence. Any consumer that rehydrates a `ReceivedOutput` from serialized bytes (cross-process handoff, storage, network messages) accepts the attacker's claim that "this output is spendable by applying `offset`".

### Impact Explanation
An unprivileged party feeding crafted bytes to `ReceivedOutput::read` can cause funds to be reported as received/spendable when they are not: the `TxOut` may pay to an unrelated script, or the `offset` may correspond to a different script than `output.script_pubkey`. A downstream signer/planner that selects this output as an input will produce a plan that either (a) cannot be signed correctly (the tweaked key doesn't match the script), or (b) produces a transaction spending an outpoint the group does not control — a transaction that can never confirm, or worse, a burned input selection. This matches the "funds reported received that are not spendable" acceptance criterion.

### Likelihood Explanation
The vulnerability is reachable wherever serialized `ReceivedOutput`s cross a trust boundary — the type exposes `serialize`/`read` as a public pair, so any deployment that persists or transmits them (rather than keeping scanner output in-process) is exposed. It requires an attacker to control or corrupt the serialized bytes, not to compromise a validator or RPC endpoint.

### Recommendation
Either:

1. Re-derive the expected script in `ReceivedOutput::read` — pass the group key (or the `Scanner`'s `scripts` map) into `read` and require `self.scripts.get(&output.script_pubkey) == Some(&offset)`, mirroring the check `scan_transaction` performs; or
2. If `read` is intended to stay context-free, add a `verify(&self, key: &ProjectivePoint) -> bool` method asserting `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)` (accounting for `register_offset`'s increment-on-odd behavior) and require callers to invoke it before treating the output as spendable.

### Proof of Concept
```rust
// Given a Scanner's group key `key` and a registered even script for offset `o`,
// craft bytes where offset = o but the TxOut pays to an arbitrary script:
let offset_bytes = o.to_bytes(); // valid scalar, passes read_F
let fake_txout = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(
    // any x-only key the attacker controls — not derived from key + G*o
    attacker_xonly,
  )),
};
// serialize(offset_bytes || fake_txout || arbitrary_outpoint)
// ReceivedOutput::read returns Ok(...) — caller believes it can spend
// `outpoint` by applying `o`, but the script does not commit to key + G*o.
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
