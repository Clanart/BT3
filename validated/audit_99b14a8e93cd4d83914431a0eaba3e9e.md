### Title
`ReceivedOutput::read` deserializes untrusted `(offset, TxOut, OutPoint)` triples without verifying the offset/script binding, yielding outputs reported as received that are not spendable - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
The peft report describes a deserialization path that trusts file contents which elsewhere go through a checked wrapper. The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs:122-134`: it accepts an arbitrary scalar `offset`, an arbitrary `TxOut`, and an arbitrary `OutPoint` from a byte stream, with no consistency check that the `offset` actually derives the private key able to spend the output's `script_pubkey`. The trusted construction path (the `Scanner`) only emits `ReceivedOutput`s after matching `script_pubkey` and registering the correct offset; the deserializer bypasses all of that validation and produces a fully-trusted object asserting spendable ownership. This is the same bug class — a "load" primitive that skips the semantic validation enforced by the code's intended construction path.

### Finding Description
`ReceivedOutput` couples three facts that are only meaningful together:

- `offset` — the scalar added to the threshold key so the output can be spent (applied via `ThresholdView`, where the offset is added to `included[0]`'s share, `crypto/dkg/src/lib.rs:518-521`).
- `output` — the `TxOut` whose `script_pubkey` must equal the tweaked `(key + offset)` key, per `p2tr_script_buf` (`networks/bitcoin/src/wallet/mod.rs:80-86`).
- `outpoint` — the claimed UTXO.

`ReceivedOutput::read` performs only `Secp256k1::read_F` for the offset and consensus-decodes the `TxOut`/`OutPoint` (`networks/bitcoin/src/wallet/mod.rs:122-134`). It never re-derives the expected `script_pubkey` from `(group key + offset)` nor checks the outpoint exists — and it cannot, because the check requires the key context the deserializer declines to take. Any byte stream therefore yields a `ReceivedOutput` the rest of the wallet treats as a spendable, owned output. The `Scanner` path establishes this binding by construction; the deserialize path silently drops it, exactly as `torch.load` drops the `weights_only` constraint the rest of peft enforces.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (an untrusted scanner/serialization source — the sink is explicitly within the reachable-input model) can inject outputs that:

- inflate the reported balance with `TxOut`s whose `script_pubkey` does not match `key + offset` — funds reported received that are not spendable, and
- cause the wallet to build transactions spending attacker-chosen `OutPoint`s that fail signature/script validation (or reference outputs belonging to third parties), corrupting coin selection and burning honest inputs' atomicity in batched spends.

Because `offset` is fully attacker-chosen, the object is indistinguishable from a legitimate scan result downstream — there is no residual integrity check.

### Likelihood Explanation
Reachability requires the integrator to deserialize `ReceivedOutput`s from data an untrusted party can influence (remote scan results, relayed blobs, restored state). That is precisely the class of input the format exists to carry, and no authentication is enforced by `read` itself. Impact is integrity-only (false spendable claims, failed spends), not key compromise, so Medium.

### Recommendation
Re-establish the invariant the `Scanner` enforces at the deserialization boundary. Either:

- make `ReceivedOutput::read` take the expected group key and verify `p2tr_script_buf(derived key) == output.script_pubkey` for the `key + offset` tweaked key, rejecting mismatches, or
- split the type into an untrusted `SerializedOutput` that must be passed through a `verify(key) -> Option<ReceivedOutput>` before use, so the "trusted" type cannot be constructed from raw bytes.

### Proof of Concept
```rust
// Attacker-controlled blob: an offset unrelated to the output's key,
// a TxOut paying to the attacker's own script_pubkey, and any outpoint.
let mut bytes = Vec::new();
bytes.extend(attacker_chosen_scalar.to_bytes());            // bogus offset
bytes.extend(consensus_serialize(&TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: attacker_p2tr_script,                    // not key+offset derived
}));
bytes.extend(consensus_serialize(&attacker_outpoint));

// ReceivedOutput::read accepts it unconditionally (mod.rs:122-134).
let output = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// Wallet now reports output.value() as received/spendable, and will
// attempt to spend `outpoint` with (key + offset) — a signature that
// cannot satisfy `attacker_p2tr_script`. No check ever fired.
```

Root cause: `ReceivedOutput::read` trusts the serialized `offset`/`output`/`outpoint` triple (`networks/bitcoin/src/wallet/mod.rs:122-134`) without the `script_pubkey`-to-`offset` binding the `Scanner` establishes, letting attacker bytes mint unspendable "received" outputs.