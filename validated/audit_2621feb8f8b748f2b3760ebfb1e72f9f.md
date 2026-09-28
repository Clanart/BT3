### Title
`ReceivedOutput::read` publishes a spendable-output record without verifying the claimed offset actually controls the output's script — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The kernel bug publishes a flow's original-direction tuple to a GC-visible hash table before the reply tuple is installed, so an observer can act on a partially-constructed flow. The Serai analog is `ReceivedOutput::read`: it reconstructs a `ReceivedOutput` — the type the wallet treats as *proof it can spend a given outpoint* — purely by decoding three attacker-controlled fields (`offset`, `output`, `outpoint`), and never checks that `key + offset * G` actually produces `output.script_pubkey`. The partially-validated object is "published" to the wallet's spendable set as if fully installed, mirroring the ordering bug where visibility precedes complete construction.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `Scanner` guarantees spendability by construction: `register_offset` only inserts scripts derived from `key + offset * G` (lines 180–196), and `scan_transaction` only creates `ReceivedOutput`s for scripts present in that map (lines 199–214). The invariant "this `ReceivedOutput`'s offset unlocks this `output`'s script" is therefore maintained by the Scanner path.

`ReceivedOutput::read` (lines 122–134) bypasses that invariant entirely:

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

Any encoding of `(scalar, TxOut, OutPoint)` — three structurally independent fields — deserializes successfully, with no check that `p2tr_script_buf(key + G*offset) == output.script_pubkey`. A `ReceivedOutput` is thus admitted into the spendable-output pipeline carrying a tuple (offset, output) whose cryptographic binding was never verified.

### Impact Explanation
A `ReceivedOutput` deserialized from untrusted bytes can claim an arbitrary `outpoint`/`output` with an unrelated `offset`. Downstream wallet code consumes `offset()`, `output()`, `outpoint()`, and `value()` to build and sign spends (`SignableTransaction::new` in `wallet/send.rs` takes `Vec<ReceivedOutput>`). The result is "funds reported received that are not spendable": the wallet credits value to an outpoint whose script either isn't controlled by the claimed offset or doesn't exist, and can be induced to construct transactions spending a non-existent or unspendable input (fee burn of real co-inputs, or signing messages for a garbage plan). Since the parsed `TxOut.script_pubkey` is fully attacker-controlled, an attacker can even point `value`/`outpoint` at a real large UTXO belonging to someone else, inflating reported balance while the crafted `offset` yields an unrelated script.

### Likelihood Explanation
Reachable by any unprivileged party who can feed bytes to `ReceivedOutput::read` — e.g., through any interface that transports or stores `ReceivedOutput::serialize()` output (DB rows, messages, backups, or peer-supplied data). It requires no keys, no validator status, and no malformed curve encodings — only canonical encodings of inconsistent fields. Impact is bounded to misreported/unspendable funds rather than key compromise, placing this at Medium severity.

### Recommendation
Bind the fields at deserialization: either (a) require the owning `Scanner`/key at read time and verify `self.scripts`/`p2tr_script_buf(key + G*offset) == output.script_pubkey`, rejecting mismatches, or (b) store the script_pubkey redundancy check inside `ReceivedOutput` itself so `read` can self-validate the offset↔script relationship without external context. At minimum, document that `ReceivedOutput::read` must only be applied to trusted bytes and that Scanner-derived outputs are the only trustworthy source.

### Proof of Concept
```rust
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;
use bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, Txid, hashes::Hash};

// Attacker-crafted encoding: offset for key K1, script_pubkey for a UTXO we don't control
let offset = Scalar::ONE;                    // unrelated to the victim script
let output = TxOut {
  value: Amount::from_sat(21_000_000_0000_0000), // claim arbitrary value
  script_pubkey: ScriptBuf::new_p2tr_tweaked(
    // victim's tweaked key — wallet has no offset mapping to this
    victim_tweaked_key,
  ),
};
let outpoint = OutPoint { txid: victim_txid, vout: 0 };

let mut buf = offset.to_bytes().to_vec();
buf.extend(bitcoin::consensus::serialize(&output));
buf.extend(bitcoin::consensus::serialize(&outpoint));

// Deserializes successfully; invariant key + offset*G == script is never checked
let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// ro.value() now reports 21M BTC as "received"; passing `ro` to
// SignableTransaction::new yields a plan spending an unspendable/nonexistent input.
```

Contrast: `scan_transaction` (mod.rs:205–211) can only emit `ReceivedOutput`s whose offset provably maps to the observed `script_pubkey`, because it looks the script up in `self.scripts`, which `register_offset` populates exclusively with derived scripts.