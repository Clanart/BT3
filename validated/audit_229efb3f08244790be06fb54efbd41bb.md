### Title
`ReceivedOutput::read` trusts an attacker-supplied key offset without verifying it corresponds to the output's script — deserialized outputs are treated as spendable when they are not (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
The CVE's bug class is a verifier honoring input that should have been rejected: a chain-of-trust check silently trusts a root the policy said not to. The analog in Serai is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`: it reconstructs a "spendable output" (`offset` + `TxOut` + `OutPoint`) entirely from untrusted bytes, with no check that the serialized `offset` actually maps the wallet's group key onto the output's `script_pubkey` — the invariant that `Scanner` enforces when producing genuine `ReceivedOutput`s. An unprivileged party feeding bytes to `ReceivedOutput::read` can therefore inject outputs that downstream wallet code believes it controls but cannot spend.

### Finding Description
`ReceivedOutput` couples three things: a scalar `offset`, a `TxOut`, and an `OutPoint`. When produced honestly, the offset is derived from `Scanner::register_offset` / `scan_transaction`, which guarantees `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` (mod.rs:180-196, 199-214). The spend path relies on exactly this: tweaking the threshold key by `offset` yields the private key behind the taproot output.

`ReceivedOutput::read` (mod.rs:122-134) reads `offset` via `Secp256k1::read_F`, then consensus-decodes the `TxOut` and `OutPoint`, and returns the struct with no consistency check:

```rust
let offset = Secp256k1::read_F(r)?;
output  = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

Any offset is accepted — including one that maps the group key to a point unrelated to `output.script_pubkey`, or to a script the attacker controls. The analogous "distrusted root" is honored: the deserializer grants trust (spendability) the `Scanner` construction would never have granted.

### Impact Explanation
A `ReceivedOutput` with a mismatched offset is "funds reported received that are not spendable": the wallet accounting treats `output.value` as controlled by `key + offset·G`, but the taproot output key is not `x_only(key + offset·G)` (or was never even-parity). When the coordinator later builds a spend over these outputs (`wallet/send.rs`), the threshold signature is computed for the wrong tweaked key — producing signatures for a key that does not control the input — so the transaction is invalid, or the accounting is poisoned into believing funds exist under an offset the attacker chose. Because `offset` is fully attacker-controlled, the attacker can also point the offset at a key they control, inverting ownership assumptions in the wallet's bookkeeping.

### Likelihood Explanation
Reachability is per the prompt's model: `ReceivedOutput::read` is listed as an accepted attacker-byte entry point, and the fuzz/receive path deserializes outputs from untrusted transport. The attack requires no secret knowledge — the attacker chooses `offset` freely — and success only requires the mismatch to go undetected, which it does since `read` performs no verification. Confidence is medium-high; the exploit ceiling is fund-accounting corruption / unspendable-received funds rather than direct theft, consistent with a High/Medium severity analog.

### Recommendation
Bind the offset to the output at deserialization time, or re-verify before use: given the wallet's base `key`, check `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)`. Since `ReceivedOutput` does not carry the base key, either store and check it, or change `read` to take the `ProjectivePoint`/`ThresholdKeys` so the invariant `Scanner` establishes cannot be bypassed by the byte-level path. At minimum, document that `ReceivedOutput` must only originate from `Scanner`, and enforce it (e.g., make `read` crate-private).

### Proof of Concept
1. Wallet base key `K` (even-Y projective point), honest output key script `S = p2tr_script_buf(K + o·G)` for legitimate offset `o`.
2. Attacker serializes: `offset' = o + d` (arbitrary `d`), `TxOut` paying to some P2TR script `S'` (e.g., attacker-owned key), any `OutPoint`.
3. `ReceivedOutput::read` accepts it (no check). Downstream code computes spend key `x + o'` and/or credits `S'`'s value to the wallet.
4. The threshold signature over `Prevouts::All` in `send.rs` produces a BIP-340 signature for internal key `x_only(K + o'·G)`, which fails taproot key-path verification against `S'` — the "received" output is unspendable, yet was counted as received, mirroring the CVE's "verify succeeds against a root that should have been distrusted."

Note: `crypto/dkg/src/pedpop.rs` was not present at the expected path, so PedPoP-side analogs (PoK binding, ECDH static IV) could not be evaluated in this pass.