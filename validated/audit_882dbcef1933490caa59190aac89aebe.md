### Title
`ReceivedOutput::read` accepts unauthenticated, unvalidated output data which is later trusted as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to APIFold's `/webhooks` endpoint accepting arbitrary unauthenticated JSON that is then stored and served as trusted state, `ReceivedOutput::read` deserializes a `ReceivedOutput` — the type Serai uses to represent coins it has received and believes it can spend — from raw bytes with zero validation. Neither the claimed scalar `offset`, the `TxOut`, nor the `OutPoint` is checked for consistency: the function never verifies that `output.script_pubkey` actually equals the P2TR script derived from `key + offset·G` (the invariant `Scanner::register_offset`/`scan_transaction` guarantees for legitimately scanned outputs), nor that the outpoint exists on-chain.

### Finding Description
`Scanner::scan_transaction` constructs `ReceivedOutput` only when `output.script_pubkey` is present in `self.scripts`, i.e., only when the output provably pays to `p2tr_script_buf(key + offset·G)` (lines 199–214, `networks/bitcoin/src/wallet/mod.rs`). `register_offset` maintains the `scripts` map so that every recorded offset satisfies that derivation (lines 180–196). However, `ReceivedOutput::read` (lines 122–134) simply reads:

1. `offset` via `Secp256k1::read_F` — any scalar,
2. `output` via `TxOut::consensus_decode` — any `script_pubkey`/value,
3. `outpoint` via `OutPoint::consensus_decode` — any txid/vout,

and returns the triple as a fully-formed `ReceivedOutput`. There is no consistency check tying `offset` to `output.script_pubkey` against the wallet's key — a check that is impossible to forge legitimately (it would require solving a discrete log) but trivial to violate when the bytes are attacker-controlled rather than scanner-produced. Any consumer that ingests serialized `ReceivedOutput`s from a channel an unprivileged party can reach (the rules sanction `ReceivedOutput::read` as an untrusted-bytes entry point) will accept injected entries as genuine receipts.

### Impact Explanation
A forged `ReceivedOutput` causes Serai to report funds as received that are not spendable — the listed valid impact class. Two concrete failure modes:

- An `output` whose `script_pubkey` does not match `key + offset·G`: the wallet credits coins it can never sign for, corrupting balance accounting and enabling an attacker to make Serai believe a deposit occurred that no one can ever claim, or to satisfy a "did we receive payment" check falsely.
- A matching script but a fabricated `outpoint`/`value`: if the outpoint doesn't exist, any transaction built from it (via the `send.rs` path) is invalid and will be rejected by the Bitcoin network, while the coordinator's ledger treats the deposit as confirmed.

Conversely, an attacker who *can* compute an offset (their own deposited output under a registered-but-attacker-known offset is fine — that's legitimate receipt) still benefits from the deserializer's silence: mismatched entries bypass every downstream check since `offset()`, `output()`, `outpoint()`, and `value()` are consumed as authoritative.

### Likelihood Explanation
Reachability depends on a caller feeding externally influenced bytes to `ReceivedOutput::read` — e.g., outputs relayed through processor message handling or resubmitted after persistence. The type is consumed in `processor/src/networks/bitcoin.rs`, which I was unable to fully trace within the tool budget; if all serialized `ReceivedOutput`s originate solely from the local `Scanner`, the practical exposure is low. The missing-validation defect in `read` itself is unambiguous, however, and mirrors the advisory's pattern (an optional verification step that is simply absent). Severity: Medium.

### Recommendation
Add a consistency check to `ReceivedOutput::read` (or a `verify(&self, key)` method callers must invoke): recompute `p2tr_script_buf(key + ProjectivePoint::GENERATOR * offset)` and reject the output unless it equals `output.script_pubkey`. Optionally, have callers confirm the `outpoint` resolves to a confirmed transaction before crediting. This restores the invariant `Scanner` enforces at production time for all deserialized data.

### Proof of Concept
```rust
// Given a wallet Scanner over `key`, an attacker crafts bytes:
let mut buf = Vec::new();
// arbitrary offset — e.g., 1
buf.extend(Scalar::ONE.to_bytes());
// TxOut paying to an attacker-controlled script, any value
attacker_txout.consensus_encode(&mut buf).unwrap();
// arbitrary outpoint (nonexistent txid is fine — nothing checks it)
fake_outpoint.consensus_encode(&mut buf).unwrap();

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// `forged` is accepted; forged.value() reports the claimed amount while
// key + offset*G does not match forged.output().script_pubkey, so it is unspendable.
```

Caveat: I could not confirm the exact production caller path that exposes `ReceivedOutput::read` to untrusted input (`processor/src/networks/bitcoin.rs` matched but wasn't read); the finding's practical exploitability hinges on that reachability.