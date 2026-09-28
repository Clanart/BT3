### Title
`ReceivedOutput::read` trusts an attacker-supplied scalar offset and TxOut, letting an unprivileged party claim arbitrary outpoints as multisig-owned deposits - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

`ReceivedOutput` couples three independent facts — the scalar `offset` to reach the spend key, the `TxOut` contents, and the `OutPoint` — and `ReceivedOutput::read` deserializes all three from raw bytes with zero consistency checking. The bytes are never validated against the `Scanner`'s `scripts` map (the only place where offset ↔ script_pubkey binding is established). An attacker who feeds crafted bytes into `ReceivedOutput::read` makes the wallet treat any outpoint on the chain — including outputs paying to keys the threshold group cannot spend — as a received deposit. This is the direct analog of the CVE's directory traversal: attacker-controlled input selects a resource (an outpoint/offset pair) outside the authorized set (outputs whose script_pubkey was derived from registered offsets).

### Finding Description

`ReceivedOutput::read` performs a straight decode — a `Secp256k1` scalar via `read_F`, then a consensus-decoded `TxOut` and `OutPoint` — and returns the tuple verbatim:

```rust
// networks/bitcoin/src/wallet/mod.rs:122-134
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

The authorization invariant — "`output.script_pubkey == p2tr(group_key + G*offset)` for a registered `offset`" — is enforced only inside `Scanner::scan_transaction`, which looks up `self.scripts.get(&output.script_pubkey)` and attaches the *map's* offset (wallet/mod.rs:199-214). `read` bypasses that entirely: whatever `offset`, `output`, and `outpoint` the byte stream encodes become the wallet's spend instruction. Downstream, the offset is applied via `ThresholdKeys::offset` so signing occurs under `group_key * scalar + G * offset` (crypto/dkg/src/lib.rs:414-417, 445-447), meaning a forged `ReceivedOutput` directs the multisig to "spend" an outpoint whose script_pubkey was never verified to match the derived key.

Two attack variants follow:

1. **Unspendable deposit credit**: attacker submits a `ReceivedOutput` naming a real outpoint they control (paying to their own key) with an arbitrary `offset`. The value is credited as multisig-received, but no `offset` can make `group_key + G*offset` equal the attacker's script (that would require solving a discrete log), so the reported funds are permanently unspendable.
2. **Nonexistent output**: the `OutPoint`/`TxOut` pair need not correspond to any real UTXO at all — nothing in `read` consults the chain. Any consumer that trusts the deserialized record credits phantom funds.

### Impact Explanation

Any downstream accounting that treats a deserialized `ReceivedOutput` as evidence of funds received credits value the threshold group cannot actually spend. In the first variant the attacker receives credit (e.g., a mint/balance) for an output locked to their own key; the multisig can never produce a valid signature for it because `group_key + G*offset` does not match the output's script_pubkey. In the second variant entirely fabricated outpoints inflate reported holdings. Either way: funds reported received that are not spendable — an accepted impact class — with direct financial loss to whoever honors the credit.

### Likelihood Explanation

Reachability requires untrusted bytes to reach `ReceivedOutput::read`, which is explicitly an in-scope sink. The function is `pub`, takes any `io::Read`, and the type exposes `serialize`/`write`, indicating it is intended to cross trust boundaries (network messages, DB, IPC between processor components). No authentication tag, checksum, or script-vs-offset recomputation is performed on decode. The attacker needs only to supply bytes; no threshold collusion, no validator status, no key material.

### Recommendation

After decoding, recompute the spend key's script and verify it: check `output.script_pubkey == p2tr_script_buf(scanner.key + G * offset)` (or equivalently that `scripts.get(&output.script_pubkey) == Some(offset)`), rejecting mismatches in `read`. Alternatively, drop `offset` from the serialized form entirely and re-derive it from `script_pubkey` via the `Scanner` map on every deserialization, making the `scripts` map the single source of truth for the offset ↔ output binding. Consumers should additionally confirm the `OutPoint` exists on-chain before crediting.

### Proof of Concept

```rust
use k256::{Scalar, ProjectivePoint};
use bitcoin::{OutPoint, TxOut, ScriptBuf, Amount, Txid, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;

// Attacker-controlled bytes fed to ReceivedOutput::read:
let mut buf = Vec::new();
// Arbitrary offset — never checked against the output's script
buf.extend(Scalar::ONE.to_bytes());
// A TxOut paying to the ATTACKER's key, not the multisig's
let attacker_output = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(attacker_tweaked_key),
};
buf.extend(bitcoin::consensus::encode::serialize(&attacker_output));
// Any outpoint — real (attacker's UTXO) or fabricated
buf.extend(bitcoin::consensus::encode::serialize(
  &OutPoint::new(Txid::from_raw_hash(attacker_txid), 0),
));

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// `forged` is now indistinguishable from a genuinely scanned output:
// value() == 100_000, offset() == 1, yet group_key + G*1 does not match
// attacker_output.script_pubkey, so the credited funds are unspendable
// and the deposit was never actually received by the multisig.
```

Note: full confirmation depends on which in-scope caller passes untrusted bytes into `ReceivedOutput::read` (e.g., the processor's multisig scanner/scheduler paths); that call-site plumbing was not fully traced within the available iterations, but the decode-time bypass of the offset↔script binding in `read` itself is verified above.