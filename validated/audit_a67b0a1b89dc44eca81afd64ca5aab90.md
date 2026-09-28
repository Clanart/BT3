### Title
Untrusted `ReceivedOutput` bytes let an attacker claim any scalar offset, making funds be treated as spendable under a key that does not control them - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes an arbitrary `Scalar` offset, a `TxOut`, and an `OutPoint` from raw bytes with no integrity check that the offset actually corresponds to the key controlling `output.script_pubkey`. The offset is later used to re-key the threshold keys when spending the output (`keys.offset(...)`-style re-keying downstream). An attacker who can feed crafted bytes into `ReceivedOutput::read` can cause the wallet/processor to treat an output as spendable when it is not (wrong offset ⇒ wrong tweaked key ⇒ unspendable input), or to misattribute an output's spending key — mirroring the "unauthorized state change" class of the reference report where an unauthenticated value silently reconfigures protocol behavior.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `ReceivedOutput` stores the scalar `offset` used to derive the effective signing key for a UTXO:

```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  output: TxOut,
  outpoint: OutPoint,
}
```

`ReceivedOutput::read` (mod.rs:122-134) reads `offset` directly via `Secp256k1::read_F(r)` and accepts whatever scalar is in the byte stream:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output = TxOut::consensus_decode(&mut buf_r).map_err(...)?;
  let outpoint = OutPoint::consensus_decode(&mut buf_r).map_err(...)?;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

The only producer that actually knows the correct offset is `Scanner::scan_transaction`, which looks up `output.script_pubkey` in its `scripts` map and attaches the registered offset (mod.rs:205-211). When a `ReceivedOutput` is instead reconstructed from untrusted bytes, nothing binds `offset` to `output.script_pubkey`: the reader has no access to the base key and performs no `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` check.

This matters because `tweak_keys` (mod.rs:46-75) and the offset machinery show the offset is semantically part of the secret: spending an output re-keys the `ThresholdKeys` by the offset. An incorrect offset produces a group key that does not match the Taproot output, so any input built from it is unspendable — yet the output's `value()` is reported and accounted as received funds.

### Impact Explanation
An unprivileged party supplying serialized `ReceivedOutput` bytes can set `offset` to an arbitrary scalar. Downstream code that treats the object as a spendable input will:

- count `output.value` as received/spendable funds while the re-keyed threshold key cannot actually authorize the spend (funds reported received that are not spendable), and/or
- produce a signature under `key + offset·G` that does not correspond to the output's `script_pubkey`, yielding an invalid transaction after the signing ceremony completes.

This is the direct analog of the reference finding: an unauthenticated, attacker-controlled value (`poolActive` there; `offset` here) silently changes security-critical state. There, anyone could flip the pool state; here, anyone who reaches the `read` path flips which key the protocol believes controls a UTXO.

### Likelihood Explanation
The sink is reachable wherever serialized `ReceivedOutput`s cross a trust boundary (e.g., bytes relayed between components/relayers rather than produced in-process by `Scanner`). Exploitation requires only crafting `offset || TxOut || OutPoint` bytes — no key material, no collusion, no validator control. The failure mode (unspendable input accepted as a valid received output) is deterministic given a malicious offset. If the deserialization path is strictly local and never exposed to untrusted bytes, the impact reduces to a defense-in-depth gap, but the in-scope threat model explicitly treats `ReceivedOutput::read` as a sink for untrusted input.

### Recommendation
Bind the offset to the output at deserialization time. Either:

- Have `ReceivedOutput::read` take the base `key` and validate `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` (with the even-Y loop semantics matching `register_offset`), rejecting mismatches; or
- Restrict `read` to trusted channels and authenticate the serialization (MAC/signature) so the offset cannot be substituted.

### Proof of Concept
```rust
// Attacker supplies bytes for a real on-chain TxOut paying to script S,
// but sets `offset` to a value that does not satisfy
// p2tr_script_buf(group_key + G*offset) == S.

let mut buf = Vec::new();
buf.extend(Scalar::ONE.to_bytes());            // bogus offset
buf.extend(serialize(&victim_txout));           // real TxOut
buf.extend(serialize(&real_outpoint));          // real OutPoint

// Succeeds: no binding of offset <-> script_pubkey is enforced
let o = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

// Consumer now believes `o.value()` is spendable, but re-keying by
// `o.offset()` produces a key that cannot authorize the spend.
```

Note on uncertainty: I could not confirm every downstream consumer of `ReceivedOutput` inside the in-scope crates; the finding relies on the documented semantics that `offset` is "the scalar offset to obtain the key usable to spend this output" and on `read` performing no consistency check, both of which are verified in `networks/bitcoin/src/wallet/mod.rs:88-134` and `180-214`.