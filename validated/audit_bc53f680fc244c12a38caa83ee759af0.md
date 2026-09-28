### Title
`ReceivedOutput::read` accepts an offset/script_pubkey pair with no consistency check, allowing unspendable outputs to be reported as wallet-owned - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` couples a scalar `offset` (the HDKD tweak needed to spend) with a `TxOut`/`OutPoint`. The only trusted constructor, `Scanner::scan_transaction`, guarantees `output.script_pubkey == P2TR(scanner.key + offset·G)` because it derives the offset from `self.scripts` keyed by `script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:199-214`). The deserializer `ReceivedOutput::read` (`mod.rs:122-134`) performs no such check — it reads an arbitrary `offset` via `Secp256k1::read_F`, then arbitrary `TxOut` and `OutPoint` bytes, and returns `Ok`. The struct does not even retain the group key needed to re-verify the relationship, so the "is this the correct depositary (script) for this offset" check is structurally absent — directly analogous to `buy` accepting any `depositary` address.

### Finding Description
- `ReceivedOutput { offset, output, outpoint }` (`mod.rs:90-97`) is the unit the wallet machinery later feeds to the FROST signing path: `offset()` is applied to the `ThresholdKeys<Secp256k1>` (via `ThresholdKeys::offset`, `crypto/dkg/src/lib.rs:414-417`, after `tweak_keys` at `mod.rs:46-75`) to derive the spending key for `outpoint`.
- `read` validates only syntactic decodability (`read_F`, `TxOut::consensus_decode`, `OutPoint::consensus_decode`), never that `output.script_pubkey` equals `p2tr_script_buf(key + G·offset)` — the invariant `Scanner` itself enforces on creation (`mod.rs:180-196`, `205-211`).
- Because the signature path is fault-tolerant in the same way the report's `rebalance` is — Schnorr/FROST signing produces a well-formed signature regardless of whether the tweaked key actually controls the prevout's script — nothing fails locally. The invalidity only materializes on-chain.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (e.g., a processor-supplied serialization of "received" outputs) can register an `OutPoint`/`TxOut` whose `script_pubkey` is not derivable from the group key under the supplied `offset`. The result is an output accounted as received/spendable that the threshold group cannot actually spend (signature invalid on the Bitcoin network), causing a collateralization-style disbalance between reported and spendable funds — the exact "funds reported received that are not spendable" impact class. Conversely, a mismatched-but-controlled script causes the group to sign a spend of an unintended input.

### Likelihood Explanation
Requires an attacker positioned to supply serialized `ReceivedOutput`s to an in-scope consumer (the scanner/honest path always produces consistent tuples, so the exposure exists only where `read` deserializes data originating from a less-trusted component or peer). Reachability is narrower than a fully public mempool path, which caps this at Medium rather than High.

### Recommendation
Either store the group key in `ReceivedOutput` and verify `output.script_pubkey == p2tr_script_buf(key + G·offset)` inside `read` (returning `Err` on mismatch, mirroring how `ThresholdKeys::read` re-validates via `ThresholdKeys::new`, `crypto/dkg/src/lib.rs:625-631`), or add a `verify(key)`/`new`-style constructor that enforces the invariant and make `read` take the key as a parameter. Document that `outpoint` existence cannot be verified off-chain and must be confirmed against the chain before signing.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs — ReceivedOutput::read never checks
// output.script_pubkey == p2tr(key + offset * G)
let offset = Scalar::ONE;                       // attacker-chosen
let output = TxOut {                            // script NOT controlled by key + offset*G
    value: Amount::from_sat(100_000).into(),
    script_pubkey: ScriptBuf::new_p2tr_tweaked(
        TweakedPublicKey::dangerous_assume_tweaked(attacker_xonly),
    ),
};
let outpoint = OutPoint::new(real_txid, 0);     // any outpoint
// Serialize offset || TxOut || OutPoint; ReceivedOutput::read returns Ok(..)
// Downstream: keys.offset(offset) + FROST signing succeeds locally, but the
// resulting Bitcoin signature is invalid for this script_pubkey -> the output
// is reported as spendable while being unspendable by the threshold group.
```
Concrete code: `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` returns `Ok` for any `read_F`-decodable scalar plus any `TxOut`, with no `p2tr_script_buf`/`needs_negation` cross-check, while the trusted path at `mod.rs:205-211` only ever emits tuples where that invariant holds.

*Caveat:* I could not inspect `wallet/send.rs` or the consumer of `ReceivedOutput::read` within the available iterations; the finding assumes (as the scope rules whitelist `ReceivedOutput::read` as an untrusted-bytes sink) that such a consumer exists. If every call site re-derives and checks the script from the known group key before signing, this reduces to a defensive gap rather than a live vulnerability.