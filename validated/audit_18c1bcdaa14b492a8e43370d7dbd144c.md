### Title
`ReceivedOutput::read` accepts an attacker-controlled scalar offset without binding it to the output's `script_pubkey`, producing unspendable "received" funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

The Git advisory (CVE-2025-48385) is about a client trusting server-advertised data without validation, letting the remote side write attacker-controlled content to an attacker-chosen location. The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs:122-134`: it deserializes the `offset` scalar, the `TxOut`, and the `OutPoint` entirely from the input byte stream, with no check that `offset` actually corresponds to the key that the output's `script_pubkey` pays to. In honest operation the invariant `output.script_pubkey == p2tr_script_buf(group_key + G * offset)` is established by `Scanner::register_offset`/`scan_transaction` (`mod.rs:180-214`), which derives the script from `key + G*offset`. The deserialization path discards that invariant: the offset is read straight from the wire (`mod.rs:123`) and stored verbatim (`mod.rs:133`), letting an attacker "inject" an arbitrary (offset, script, outpoint) triple into wallet state.

### Finding Description

`ReceivedOutput` is the unit of "funds received" for the Bitcoin wallet. Its authority is the `offset` field: `SignableTransaction`/the multisig signing path uses `output.offset()` to derive the per-input signing key (`key + G * offset`), exactly as `register_offset` established the relationship between offset and script (`mod.rs:185-191`, test `wallet.rs:219-222` shows spending derives `key + G*offset`).

`Scanner::scan_transaction` only ever produces `ReceivedOutput`s where the offset was looked up in `self.scripts` keyed by the on-chain `script_pubkey`, so scanned outputs are always consistent. `ReceivedOutput::read`, however, performs no such check:

```rust
// networks/bitcoin/src/wallet/mod.rs:122-134
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output = TxOut::consensus_decode(&mut buf_r)...;
  let outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

There is also no "re-derive script from offset and compare" step anywhere downstream: `p2tr_script_buf` is available in the same module (`mod.rs:80-86`) and the scanner key is a fixed value, so the check is cheap and was simply omitted.

Additionally, a mismatch in the other direction is possible: the attacker's `outpoint` need not reference the `output`/`script_pubkey` at all — the three fields are independently attacker-controlled, so the deserialized object can claim any on-chain UTXO under any offset.

### Impact Explanation

An unprivileged party who can feed bytes to `ReceivedOutput::read` (e.g., any path where received outputs are relayed, persisted, or reconstructed from peer-supplied data) can cause the wallet to accept a `ReceivedOutput` whose offset does not match the output's actual key. Two concrete consequences:

1. **Funds reported received that are not spendable.** Attacker supplies a real-looking `TxOut`/`OutPoint` with a random offset. The wallet records the balance as received, but `key + G*offset` does not correspond to the output's script key, so the threshold signature produced when spending it will not satisfy the output. The "received" funds are phantom/unspendable — the exact "funds reported received that are not spendable" impact class.
2. **Attempts to spend foreign/nonexistent UTXOs.** With an attacker-chosen `outpoint`, the wallet will construct and sign a transaction spending an outpoint it does not control, producing an invalid transaction and potentially stalling the honest output queue (a poisoned input blocks the whole `SignableTransaction` since fee/change math is computed over it).

Severity: **Medium** — integrity impact on received-funds accounting and spendability, reachable only where serialized `ReceivedOutput`s cross a trust boundary.

### Likelihood Explanation

Whether this is exploitable in deployment depends on integrators deserializing `ReceivedOutput` from untrusted input rather than only from `Scanner` results; the API is `pub` and `#[cfg(feature = "std")]`-gated for external use. It is not exploitable purely by sending an on-chain transaction (those go through `scan_transaction`, which enforces the invariant). Confirmed gaps: no offset↔script binding in `read` (`mod.rs:122-134`), no validation elsewhere in the wallet module. Unverified: the exact upstream callers in `processor/` that call `ReceivedOutput::read` on network-provided bytes — if none exist, impact is limited to integrator misuse of a deserialization API.

### Recommendation

Bind the offset to the output at deserialization or at spend time: pass the scanner/group key into `ReceivedOutput::read` (or add `ReceivedOutput::verify(key)`) and reject unless `p2tr_script_buf(key + G * offset) == Some(output.script_pubkey)`. At minimum, document that `ReceivedOutput`s must originate from `Scanner` and never be deserialized from untrusted input.

### Proof of Concept

```rust
use bitcoin::{TxOut, OutPoint, Amount, ScriptBuf, hashes::Hash, Txid};
use frost::curve::Secp256k1;
use ciphersuite::Ciphersuite;
use serai_bitcoin::wallet::ReceivedOutput;
use k256::Scalar;
use std::io::Write;

// Craft a ReceivedOutput claiming victim_key's UTXO but with an offset that
// derives a completely different key.
let mut buf = vec![];
// offset: arbitrary scalar, NOT the one matching output.script_pubkey
buf.write_all(&Scalar::from(42u64).to_bytes()).unwrap();
// output: a TxOut paying to SOMEONE ELSE's p2tr script (any ScriptBuf)
let fake_txout = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(/* victim key */),
};
buf.write_all(&bitcoin::consensus::serialize(&fake_txout)).unwrap();
// outpoint: any real or fabricated outpoint on chain
buf.write_all(&bitcoin::consensus::serialize(
  &OutPoint::new(Txid::all_zeros(), 0),
)).unwrap();

// Deserialization succeeds with no consistency check
let stolen = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// `stolen` is now accepted as a received output; when spent, the multisig
// derives group_key + G * 42, whose signature will not satisfy
// output.script_pubkey -> reported funds are unspendable / spend fails.
```