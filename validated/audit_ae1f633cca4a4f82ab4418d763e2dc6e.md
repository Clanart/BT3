### Title
`ReceivedOutput::read` accepts attacker-controlled bytes without binding the spend offset to the output — forged/unspendable outputs reported as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The DNN advisory describes a dangerous capability reachable by unauthenticated input because a default endpoint performs no authentication on what it accepts. The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`: it deserializes an attacker-supplied byte stream into a `ReceivedOutput` — the object the wallet layer treats as authoritative proof that funds were received and are spendable — while performing no check that the embedded `offset` scalar actually derives the `output`'s `script_pubkey`, nor that `outpoint` refers to a real on-chain UTXO. Any party able to feed bytes into this `read` path can cause arbitrary outputs to be reported as received funds that are not spendable.

### Finding Description
`ReceivedOutput::read` parses three independent fields and assembles the struct with zero semantic validation [1](#0-0) :

- `offset` via `Secp256k1::read_F` (canonical scalar, but otherwise unconstrained),
- `output` via `TxOut::consensus_decode` (arbitrary `script_pubkey` and `value`),
- `outpoint` via `OutPoint::consensus_decode` (arbitrary txid/vout).

The only producer that establishes the offset↔script invariant is `Scanner::scan_transaction`, which derives `offset` from its internal `scripts` map keyed by `script_pubkey` [2](#0-1) . `read` bypasses this entirely: the offset-to-key relationship (`self.key + GENERATOR * offset` → even-Y P2TR script, enforced in `register_offset` [3](#0-2) ) is never re-verified on deserialization.

Downstream, `SignableTransaction::new` trusts `input.offset` and `input.output.value` to compute funding totals and store prevouts [4](#0-3) , and `multisig` keys each input's signing machine by `keys.offset(self.offsets[i])` [5](#0-4) . The only consistency check is `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, which rejects mismatched offsets at signing time — meaning a forged `ReceivedOutput` is still accepted into the wallet's bookkeeping as received value but fails (or worse, misdirects) when spent.

### Impact Explanation
An unprivileged party who supplies bytes to `ReceivedOutput::read` (e.g., via any sync/DB/rpc-import path that round-trips serialized `ReceivedOutput`s) can:

- Report funds received that are not spendable: a real-looking `TxOut`/`OutPoint` paired with an offset that does not derive the script passes deserialization and inflates the reported balance (`value()` reads the attacker-chosen `TxOut.value`), yet `multisig()` returns `None` or produces a transaction that cannot be signed, stranding the recorded output.
- Construct a `ReceivedOutput` whose `offset` derives a *different* script than a real deposit's, redirecting which key path the wallet believes controls a given outpoint.

This matches the accepted impact class "funds reported received that are not spendable" and mirrors the DNN root cause: an input-accepting path reachable without authentication lacks a default-deny validation step.

### Likelihood Explanation
Reachability requires an attacker to inject bytes into a `ReceivedOutput::read` consumer (deserialization sink, not a consensus path). It requires no key material, no threshold collusion, and no validator privileges — only control over untrusted bytes, which the audit scope explicitly counts as reachable. Exploitation is deterministic: any `(offset, output, outpoint)` triple is accepted.

### Recommendation
Make `ReceivedOutput` construction authenticate the offset↔output binding. Either remove public `read` in favor of deserialization that re-derives `p2tr_script_buf(key + GENERATOR * offset)` and compares it to `output.script_pubkey` (requires passing the base `key` or `Scanner`), or add a `verify(key) -> bool` method and document that callers must check it before treating a deserialized `ReceivedOutput` as spendable. This is the analog of the DNN fix: change the default posture so unauthenticated input is rejected unless explicitly validated.

### Proof of Concept
```rust
// networks/bitcoin: any byte stream is accepted as a "received" output
let offset = Scalar::ZERO; // claims the base key controls it
let fake = TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: ScriptBuf::new_p2tr_tweaked(/* attacker-chosen key */),
};
let mut bytes = offset.to_bytes().to_vec();
bytes.extend(serialize(&fake));
bytes.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));

let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(ro.value(), 1_000_000); // reported as received funds

// SignableTransaction::new accepts it; multisig() later returns None
// because offset.group_key() doesn't match fake.script_pubkey —
// the output is recorded yet permanently unspendable.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-195)
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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-282)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }
```
