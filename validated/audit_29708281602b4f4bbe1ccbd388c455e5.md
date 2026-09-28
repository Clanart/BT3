### Title
Spoofed `ReceivedOutput` accepted without verifying the offset/script binding - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the spoofed-deposit bug (a callback records a claimed asset ID without verifying actual ownership/transfer), `ReceivedOutput::read` accepts an arbitrary `(offset, TxOut, OutPoint)` triple from untrusted bytes and never verifies that `output.script_pubkey` actually equals the P2TR script for `key + G * offset`, nor that the outpoint exists. The only producer that performs this check is `Scanner::scan_transaction`, which matches `script_pubkey` against the registered script table. The deserializer bypasses that check entirely.

### Finding Description
`ReceivedOutput` is the structure the wallet uses to claim "we own this output and can spend it with `offset`":

- `ReceivedOutput::read` reads `offset` via `Secp256k1::read_F`, then a `TxOut` and an `OutPoint` with consensus decoding, and returns them unconditionally — there is no consistency check between `offset` and `output.script_pubkey` and no check the outpoint is real/confirmed.
- Contrast with `Scanner::register_offset`/`scan_transaction`, which build `ReceivedOutput` only after `self.scripts.get(&output.script_pubkey)` succeeds, i.e., the offset is proven to derive the script.
- The downstream spend path (`TransactionSignMachine::sign` in `networks/bitcoin/src/wallet/send.rs`) signs sighashes over `self.tx.prevouts` and witnesses the inputs blindly; it trusts that each `ReceivedOutput` was produced by the scanner.

An unprivileged party who can feed bytes into `ReceivedOutput::read` (e.g., a coordinator/peer supplying outputs for the signer set) can claim ownership of an output whose script is anyone else's — including an arbitrary script-path output — or of an outpoint that was never mined.

### Impact Explanation
Funds reported received that are not spendable: the signer set will produce threshold signatures for an input it cannot actually claim (wrong offset → the tweaked key doesn't match the output key, or the outpoint doesn't exist / is already spent / is a script-path-locked output). For a real Bitcoin tx this results in an invalid, unbroadcastable transaction or burned fees, and can wedge the signing session. This mirrors the report's impact — state corruption by recording a deposit/claim of an asset that was never actually owned, detected only after the fact.

### Likelihood Explanation
Reachable whenever `ReceivedOutput::read` is applied to bytes not produced locally by `Scanner::scan_transaction`/`scan_block`. The offset/script binding check exists in exactly one place (the scanner) and is silently skipped in the deserialization path, so any integration that round-trips `ReceivedOutput` through untrusted channels (signer messages, DB entries written from peer data) exposes it. A single crafted 32-byte offset plus any `TxOut`/`OutPoint` suffices; no mining or signing power is needed to trigger the inconsistency.

### Recommendation
Add the ownership check to `ReceivedOutput` itself (or a validating constructor): recompute `p2tr_script_buf(key + G * offset)` and require it to equal `output.script_pubkey` before accepting the output — the same check `Scanner` performs implicitly via its script table. If `read` has no access to the base key, add a `ReceivedOutput::verify_against(key)` method and require callers to invoke it, or make `read` private to `Scanner`.

### Proof of Concept
```rust
// Any key the scanner would use
let key = ProjectivePoint::GENERATOR * Scalar::from(42u64);

// Attacker picks an output they do NOT own: e.g., someone else's P2TR script
let victims_script = ScriptBuf::new_p2tr_tweaked(
    TweakedPublicKey::dangerous_assume_tweaked(x_only(&ProjectivePoint::GENERATOR)),
);

let mut bytes = vec![];
// Arbitrary offset — does NOT derive victims_script from `key`
bytes.extend(Scalar::ZERO.to_bytes());
bytes.extend(serialize(&TxOut { value: Amount::from_sat(100_000), script_pubkey: victims_script }));
bytes.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));

// Accepted unconditionally — no check that offset derives the script
let spoofed = ReceivedOutput::read::<&[u8]>(&mut bytes.as_ref()).unwrap();
// `spoofed` is now indistinguishable from a scanner-produced output and will be
// fed into TransactionSignMachine::sign, producing signatures over an
// unspendable input (invalid tx / wasted fee / stalled session).
```

Supporting code: `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` performs no offset↔script verification; the only binding check lives in `Scanner::scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:205-211`; blind signing over the claimed inputs occurs in `TransactionSignMachine::sign` at `networks/bitcoin/src/wallet/send.rs:373-397`.