### Title
`ReceivedOutput::read` accepts untrusted offset/script/outpoint triples without verifying the offset actually derives the output's key — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to the reference bug — where a token transfer "succeeds" against an address whose validity was never checked, crediting an internal balance that does not correspond to anything real — `ReceivedOutput::read` deserializes an `(offset, TxOut, outpoint)` triple with no check that `output.script_pubkey == p2tr_script_buf(key + G * offset)`. `Scanner::scan_transaction` is the only constructor that establishes this binding, and the deserializer bypasses it entirely. Any consumer that trusts a deserialized `ReceivedOutput` will account for (and attempt to spend) funds that are not actually spendable under the claimed offset.

### Finding Description
`ReceivedOutput` encodes three independent claims: a scalar `offset`, a `TxOut`, and an `OutPoint`. The security-critical invariant is that the output's `script_pubkey` is the P2TR script of `key + G*offset` for the wallet's group key — i.e., that the offset genuinely "unlocks" that script. `scan_transaction` enforces this by construction: it only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns an offset that was registered for exactly that script (`networks/bitcoin/src/wallet/mod.rs:199-214`).

`ReceivedOutput::read` reconstructs the same type from raw bytes (`mod.rs:122-134`):

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output;
  let outpoint;
  {
    let mut buf_r = BufReader::with_capacity(0, r);
    output = TxOut::consensus_decode(&mut buf_r)...;
    outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  }
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

Only syntactic validity is checked: the offset must be a canonical scalar and the `TxOut`/`OutPoint` must consensus-decode. Nothing binds `offset` to `output.script_pubkey`. Like the reference bug — where `safeTransferFrom` to a codeless address silently "succeeds" and credits internal accounting — deserialization silently "succeeds" and produces an object the rest of the wallet treats as spendable balance, when no key derived from `key + offset*G` can satisfy the output's script.

The downstream spend path compounds this: `tweak_keys`/`register_offset`/`Scanner` assume the offset↔script invariant, and `SignableTransaction` signs each input under the key derived from the output's claimed offset. A forged `ReceivedOutput` therefore directs a real threshold signature at an input whose witness will never validate (wrong key for the script), or — since `script_pubkey` is fully attacker-controlled — at an output paying to an arbitrary script, while the wallet's accounting reports it as a received balance.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (the type is explicitly in the untrusted-deserialization surface: `Output::read` in `processor/src/networks/bitcoin.rs` embeds `ReceivedOutput::read` on externally parsed data) can:

- Cause the wallet/processor to report funds as received that are not spendable — phantom balance credited to internal accounting, matching the accepted impact class.
- Trigger a FROST signing round over a `SignableTransaction` that includes the forged input. The honest signers produce a valid share/signature for a transaction that is unspendable on-chain (the derived key does not match the script), wasting a signing session and burning any real co-inputs' fees if the transaction is broadcast with mixed inputs.

This is integrity loss on the wallet's balance/spend path, not a mere parsing inconvenience: the object type itself asserts spendability, and nothing downstream re-derives the script from the offset.

### Likelihood Explanation
Reachability requires attacker influence over bytes fed to `ReceivedOutput::read`. Within Serai's architecture these objects move between scanner, DB, and coordinator as serialized blobs; any untrusted or corrupted input path (coordinator-supplied output lists, restored/mutated DB entries, cross-network message deserialization via `Output::read`) reaches the bug with fully public, attacker-chosen bytes — no key material, no validator collusion, no RPC trust needed. The offset, `TxOut`, and `OutPoint` are all free-form attacker input. Exploitation does not require race conditions or special timing, only a deserialization surface. Impact per instance is bounded (phantom crediting / invalid spend rather than direct theft), consistent with Medium.

### Recommendation
Bind the offset to the script at deserialization or consumption time. Since `ReceivedOutput::read` lacks the wallet's base key, the cheapest fix is to carry and verify the relationship at the point of use: before an output enters `SignableTransaction` or balance accounting, recompute `p2tr_script_buf(key + G * offset)` and require equality with `output.script_pubkey` (returning `None`/error for odd or mismatched points, mirroring `register_offset`'s handling). Alternatively, store the base key alongside received outputs so `read` can self-verify, or restrict `ReceivedOutput` construction to `Scanner` and make the deserializer verify against a `Scanner`'s `scripts` map rather than returning a bare struct.

### Proof of Concept
```rust
use bitcoin::{TxOut, OutPoint, Txid, ScriptBuf, Amount, hashes::Hash};
use k256::{Scalar, ProjectivePoint, elliptic_curve::group::Group};
use serai_bitcoin::wallet::ReceivedOutput;

// Attacker-controlled bytes:
// - offset: any scalar, e.g. 1
// - script_pubkey: a P2TR script for a key the wallet does NOT control
//   (e.g. G*0xdeadbeef's p2tr_script_buf, or any 34-byte v1 witness program)
// - outpoint: any txid:vout, real or fabricated
let offset = Scalar::ONE;
let evil_key = ProjectivePoint::GENERATOR * Scalar::from(0xdeadbeefu64);
let script = serai_bitcoin::wallet::p2tr_script_buf(evil_key).unwrap();

let mut buf = vec![];
buf.extend(offset.to_bytes());
buf.extend(bitcoin::consensus::encode::serialize(&TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: script,
}));
buf.extend(bitcoin::consensus::encode::serialize(
  &OutPoint::new(Txid::all_zeros(), 0),
));

// Deserialization succeeds despite key + 1*G != evil_key
let received = ReceivedOutput::read::<&[u8]>(&mut buf.as_ref()).unwrap();
assert_eq!(received.value(), 100_000); // reported as spendable balance
```

The resulting `ReceivedOutput` claims `offset = 1` unlocks a script paying to `G*0xdeadbeef`. Scanned outputs can never have this shape — `scan_transaction` only emits offsets registered for the exact script — yet the deserializer accepts it. Feeding it into `SignableTransaction::new` makes signers produce shares for an input whose witness key (`key + 1*G`) does not match the script, yielding a permanently invalid spend and phantom credited balance, exactly the "supply to a nonexistent token updates internal accounting" primitive mapped onto Serai's output-tracking shape.