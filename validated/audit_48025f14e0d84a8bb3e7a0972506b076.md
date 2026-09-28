### Title
`ReceivedOutput::read` deserializes an attacker-chosen scalar offset and `TxOut` without verifying the offset actually derives the output's `script_pubkey` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is "untrusted input consumed as trusted parameters without validation." The analogous shape in Serai is `ReceivedOutput`, the wallet's spendable-output record: it pairs a scalar `offset` (which re-keys the threshold group key at spend time) with a `TxOut`/`OutPoint`, and `ReceivedOutput::read` accepts both from raw bytes with no consistency check binding them together.

### Finding Description
`Scanner` is the authoritative mapping from observed `script_pubkey` to offset: `scan_transaction` only emits a `ReceivedOutput` whose `offset` is the registered scalar satisfying `p2tr_script_buf(self.key + G*offset) == output.script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:199-214`). That invariant is what makes the output spendable — `SignableTransaction`/`multisig` re-keys the FROST `ThresholdKeys` via `ThresholdKeys::offset`/`scale` (`crypto/dkg/src/lib.rs:414-417`, `445-447`), which shifts `group_key()` by `G*offset`, so the produced BIP-340 signature is only valid for an output paying to `key + offset*G`.

`ReceivedOutput::read` (`wallet/mod.rs:122-134`) deserializes `offset` via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via consensus decoding, then returns the struct **without re-deriving** `p2tr_script_buf(key + G*offset)` and comparing it to `output.script_pubkey`. The invariant is enforced on the scan path but silently assumed on the deserialization path — the same class as the advisory: attacker-controlled input is trusted where validation is skipped.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` can cause Serai to report a `ReceivedOutput` that is not actually spendable by the intended key: any `TxOut` paying to an arbitrary P2TR key paired with an arbitrary scalar `offset`. The wallet then treats the output as a spendable input; the threshold signing round completes successfully (each share verifies against the locally-computed tweaked key, `crypto/frost/src/sign.rs:447-496`), but the resulting transaction's witness fails Bitcoin consensus because `key + offset*G` does not equal the output's real script key. Funds are reported received that are not spendable, and the input-selection/fee math in `SignableTransaction::new` treats the phantom value as real balance. Conversely, pairing a *real* scanner output with a *wrong* offset produces signatures under the wrong tweaked key.

### Likelihood Explanation
Reachability is limited to contexts where `ReceivedOutput` bytes cross a trust boundary (restoration from a backup/peer, relayed output records, `Output::read` in `processor/src/networks/bitcoin.rs:145-166` which wraps `ReceivedOutput::read`). It is not exploitable purely via on-chain transactions — the scanner path is sound. That caps this at Medium rather than High. Note I did not confirm whether downstream `SignableTransaction`/`multisig` construction re-validates the offset↔script binding; if it does, the residual impact is reduced to misreported balances.

### Recommendation
Either (a) make `ReceivedOutput` unforgeable by construction — store only the outpoint/script and re-derive the offset from a `Scanner` at use — or (b) add a `ReceivedOutput::verify(&self, key)` / check inside `SignableTransaction::new` asserting `p2tr_script_buf(key + G*offset) == Some(output.script_pubkey)` before the output is treated as spendable, mirroring the check `scan_transaction` performs implicitly.

### Proof of Concept
```rust
// Construct a forged ReceivedOutput: a TxOut paying to an attacker-chosen
// P2TR key, paired with offset ZERO (claims "belongs to the base key").
let mut buf = Vec::new();
buf.extend(Scalar::ZERO.to_bytes());                 // offset = 0
buf.extend(serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: ScriptBuf::new_p2tr_tweaked(
        TweakedPublicKey::dangerous_assume_tweaked(attacker_xonly),
    ),
}));
buf.extend(serialize(&OutPoint::new(fake_txid, 0)));

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted

// forged is indistinguishable from a scanned output, but signing for it
// under key + 0*G produces a witness that fails script verification —
// the "received" funds are not spendable by this wallet.
```