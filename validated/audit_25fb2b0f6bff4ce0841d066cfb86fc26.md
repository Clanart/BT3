### Title
Attacker-controlled `offset` in `ReceivedOutput` is never bound to the output's script, causing spends under the wrong key - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` pairs a Bitcoin `TxOut`/`OutPoint` with a scalar `offset` that tells the FROST signers which offset key (`group_key + offset·G`, via `ThresholdKeys::offset`) must produce the BIP-340 signature for that input. `ReceivedOutput::read` deserializes `offset` directly from attacker-supplied bytes with no check that `output.script_pubkey` actually commits to `key + offset·G`. This mirrors the Discourse bug class: an unprivileged party can attach an arbitrary field to an object whose severity depends on downstream consumers — here, the signing path that re-keys the multisig per input.

### Finding Description
`Scanner::scan_transaction` produces `ReceivedOutput`s whose `offset` is looked up from the scanner's own `scripts` map, so honestly generated objects are consistent (networks/bitcoin/src/wallet/mod.rs:199-214). However, `ReceivedOutput::read` accepts untrusted bytes and stores whatever scalar `Secp256k1::read_F` yields, without ever recomputing `p2tr_script_buf(key + offset·G)` and comparing it to `output.script_pubkey` (networks/bitcoin/src/wallet/mod.rs:122-133).

```rust
// networks/bitcoin/src/wallet/mod.rs
let offset = Secp256k1::read_F(r)?;          // arbitrary attacker scalar
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

The offset field is later consumed by the Bitcoin send path (`networks/bitcoin/src/wallet/send.rs`), which applies it as a per-input key tweak using `ThresholdKeys::offset`/`scale` semantics (crypto/dkg/src/lib.rs:414-417) — the offset is added into `included[0]`'s effective share during signing per the FROST extension spec (spec/cryptography/FROST.md:39-43). Nothing upstream re-derives the offset from the script.

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` can:

- Attach `offset = o` where `key + o·G ≠ ±` the deposit's actual output key. The signing session then produces a BIP-340 signature under the wrong tweaked key; the resulting spend transaction fails script validation and the plan must be aborted/retried — funds that were reported received cannot be spent through this flow (denial of spend / fee burn).
- Combined with the surjective nature of `register_offset` (networks/bitcoin/src/wallet/mod.rs:180-196), mismatched offsets break the invariant that `offset` is the unique pre-image of the output script, so blame/retry logic operates on corrupted per-input keying.

### Likelihood Explanation
Reachability requires attacker-controlled bytes reaching `ReceivedOutput::read` (explicitly listed as an in-scope untrusted input). The parser performs only scalar-canonicality checks inside `read_F` and syntactic consensus decoding of `TxOut`/`OutPoint`; there is no semantic validation tying `offset` to `output`. The impact is integrity/availability of spends rather than key recovery, consistent with a Medium rating.

### Recommendation
Make `ReceivedOutput` self-validating: either
1. Store no offset and recompute it at use time via `Scanner`'s `scripts` map (`script_pubkey -> offset`), or
2. On `read`/`scan`, verify `p2tr_script_buf(self.key + GENERATOR * offset) == Some(output.script_pubkey)` (allowing the negation case since x-only keys are parity-insensitive) and reject otherwise.

### Proof of Concept
```rust
// Attacker crafts a ReceivedOutput: a real deposit UTXO paying to the
// multisig's base p2tr script (offset = 0), but with offset = 7.
let mut bytes = vec![];
bytes.extend(Scalar::from(7u64).to_bytes());       // arbitrary offset field
bytes.extend(serialize(&txout_to_base_key_script)); // script_pubkey = p2tr(key)
bytes.extend(serialize(&outpoint));

let ro = ReceivedOutput::read(&mut &bytes[..]).unwrap(); // accepted
// Later, send.rs signs this input with keys.offset(Scalar::from(7))
// (plus taproot tweak), producing a BIP-340 signature for
// x_only(key + 7*G) which does not match the output's x_only(key).
// The broadcast transaction fails CHECKSIG; the deposit is unspendable
// via this plan until the corrupted ReceivedOutput is discarded.
```
Root cause: `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-133) deserializes `offset` as an unbound "custom field"; the only producer that enforces the offset↔script binding is `Scanner` (lines 199-214), and that invariant is not re-established on deserialization.