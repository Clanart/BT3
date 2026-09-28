### Title
`ReceivedOutput::read` accepts any (offset, output, outpoint) triple without verifying the offset actually derives the output's `script_pubkey`, so fabricated metadata makes unspendable outputs appear received — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to the `FloorPriceFeedAdaptor` returning `latestRoundData()` with all auxiliary fields zeroed so downstream staleness validation cannot work, `ReceivedOutput::read` deserializes a scalar `offset`, a `TxOut`, and an `OutPoint` from raw bytes and returns them as a trusted "spendable output" without ever checking that `key + offset·G` produces the output's `script_pubkey`. The consistency metadata that makes a `ReceivedOutput` meaningful is neither carried in the encoding nor reconstructed/verified on read, so a downstream consumer must either blindly trust it or skip validation entirely.

### Finding Description
`Scanner::scan_transaction` builds `ReceivedOutput`s internally and guarantees the invariant `p2tr_script_buf(self.key + GENERATOR * offset) == output.script_pubkey`, because the offset is looked up in `self.scripts` keyed by the observed `script_pubkey` (wallet/mod.rs, `scan_transaction`, lines 199–214; `register_offset`, lines 180–196).

`ReceivedOutput::read` (wallet/mod.rs, lines 122–134), however, deserializes the three fields independently:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

There is no scanner/key context parameter and no post-deserialization check that `offset` corresponds to `output.script_pubkey`. Whoever supplies the bytes fully controls which key the wallet believes controls the output. `write`/`serialize` (lines 137–148) likewise emit only the raw triple, so the binding between offset and script exists nowhere in the format — exactly the same shape as an adaptor emitting `(0, price, 0, 0, 0)` where the fields consumers validate against are fabricated.

### Impact Explanation
A `ReceivedOutput` is the structure later consumed by `send.rs` to construct and sign spends: the offset is used to derive the signing key (`tweak`/`offset` path in the wallet) while the `TxOut`/`OutPoint` select the UTXO. If an unprivileged party can feed crafted bytes to `ReceivedOutput::read`, they can report outputs as received that are not spendable by the group key (attacker-chosen `script_pubkey` with `offset = 0`, which is the honest offset for the base key's script), causing funds to be accounted as received while any attempted spend produces a signature for the wrong key — the "funds reported received that are not spendable" acceptance criterion. Conversely, pairing a legitimately-owned `script_pubkey` with a wrong offset produces unspendable-by-construction entries that silently corrupt wallet bookkeeping.

### Likelihood Explanation
Exploitation requires untrusted bytes reaching `ReceivedOutput::read`, i.e., a deployment where serialized outputs transit a channel an attacker can influence (e.g., a coordinator or peer distributing scanned outputs rather than each node scanning independently). Within the audited rules this is a permitted reachability assumption, but if `ReceivedOutput`s are only ever produced and consumed locally by `Scanner`, there is no attacker-controlled input path and the issue is inert. Because the encoding commits to nothing, the check also cannot be retrofitted at read time without the scanner's key.

### Recommendation
Either (a) include the `script_pubkey`-to-offset binding in the serialized form and verify it on read — e.g., pass the scanning `ProjectivePoint` into `read` and assert `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)` (mirroring `register_offset`'s increment-until-even logic for non-base offsets) — or (b) serialize only the `OutPoint`/`TxOut` and always re-derive the offset via `Scanner`, eliminating trust in the encoded offset entirely. The fix parallels the oracle remediation: populate and validate the auxiliary fields rather than emitting unchecked defaults.

### Proof of Concept
1. Craft bytes: `offset = Scalar::ZERO` encoding, a `TxOut` whose `script_pubkey` pays to an attacker-controlled Taproot key, any `OutPoint`.
2. `ReceivedOutput::read(&mut bytes)` returns `Ok` with `offset() == 0` (wallet/mod.rs:122-134).
3. Downstream code treats it as an output spendable by the base group key; the constructed spend signs for `key + 0·G` which does not match the attacker script, yielding an unspendable/invalid spend while the wallet recorded the funds as received.