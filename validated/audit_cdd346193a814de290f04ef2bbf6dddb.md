### Title
`ReceivedOutput::read` accepts an unbound offset — a deserialized output's "owner" scalar is never verified against the output's `script_pubkey` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` maintains a `HashMap<ScriptBuf, Scalar>` binding each watched `script_pubkey` to the scalar offset that produces the spending key. When outputs are discovered on-chain via `scan_transaction`, the `ReceivedOutput` produced is internally consistent: `self.key + G·offset` is guaranteed to yield `output.script_pubkey`. However, `ReceivedOutput::read` deserializes the three fields (`offset`, `output`, `outpoint`) independently and performs no binding check between them. This is the direct analog of the CVE-2026-57956 IDOR: an object (the wallet output) is referenced by an identifier (the scalar offset) without verifying that the referenced object belongs to the referencer's scope (that the offset actually derives the key the output pays to). Any untrusted party that can cause a `ReceivedOutput` to be deserialized — the `read` path is explicitly a public-input surface — can inject outputs the wallet will treat as spendable but which are not, or misattribute the spend key.

### Finding Description
`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reads:

- `offset` via `Secp256k1::read_F` — an arbitrary scalar,
- `output` (TxOut) via `consensus_decode` — arbitrary `script_pubkey` and value,
- `outpoint` — arbitrary txid/vout.

It then constructs `ReceivedOutput { offset, output, outpoint }` with no relationship check. The correct invariant, established in `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:205-211`), is that `self.scripts.get(&output.script_pubkey)` yields exactly the offset such that `p2tr_script_buf(key + G·offset) == output.script_pubkey`. The deserializer replicates none of this; indeed `ReceivedOutput` does not even carry the base `key`, so nothing in the type enforces the binding. The `offset` is treated as authoritative for "which key spends this output" — the same pattern as the SigNoz bug where a caller-supplied UUID selected an alert rule with no organization filter.

### Impact Explanation
`send.rs` uses `output.offset()` to re-key the signing material per input. A `ReceivedOutput` crafted by an attacker with either of the following breaks spendability or accounting:

1. **Unspendable-funds injection**: a real on-chain output paying one of the wallet's scripts (the attacker can learn all watched scripts by scanning the chain themselves) paired with a *different* offset — e.g., `offset = Scalar::ZERO` (the base-key offset) against an output that actually requires offset `x`. The wallet reports the funds as received, builds a transaction, derives `key + G·0` as the input key, and produces a Schnorr signature that does not satisfy `output.script_pubkey`. The transaction is unspendable/invalid; funds are reported received that are not spendable — an accepted impact class.

2. **Phantom deposits**: `output` paying an unrelated script, with any offset. The wallet counts the value as its own balance. Any downstream accounting that trusts `ReceivedOutput::value()` is corrupted.

Because Taproot input signatures commit to the spent output (`Prevouts::All`), the mismatch is not caught until signature verification fails at the network level, and blame cannot be localized to the malformed input.

### Likelihood Explanation
Reachability requires an untrusted party to supply bytes to `ReceivedOutput::read` — e.g., a peer contributing claimed inputs to a signing session or any protocol carrying serialized outputs. `read` is `pub` and is on the stated public-input surface. No key material, validator status, or collusion is needed; the attacker only needs to know watched script_pubkeys, which are public on-chain. The failure mode is integrity of the wallet's UTXO set rather than key extraction, consistent with Medium severity.

### Recommendation
Either:

- Make `ReceivedOutput::read` take the base `key: &ProjectivePoint` and verify `p2tr_script_buf(*key + ProjectivePoint::GENERATOR * offset) == Some(output.script_pubkey)`, rejecting mismatches; or
- Store/derive the offset implicitly: deserialize only `(output, outpoint)` and recover the offset from `Scanner::scripts` via `output.script_pubkey`, returning an error when the script is not a registered script — making it impossible to express a `ReceivedOutput` the scanner would not itself have produced.

The second option mirrors the SigNoz fix (scope the lookup to the caller's tenant) most closely: the offset should be *looked up* from the wallet's own registered map keyed by `script_pubkey`, never *taken* from attacker-controlled bytes.

### Proof of Concept
```rust
// Wallet watches base key K; attacker knows script S = p2tr(K + G*x) for
// a registered offset x (public on-chain).
// Attacker serializes: offset = Scalar::ZERO, output = TxOut{ value, script_pubkey: S },
// outpoint = real OutPoint of the output paying S.
let mut buf = vec![];
buf.extend(Scalar::ZERO.to_bytes());           // wrong offset
buf.extend(serialize(&real_txout_paying_S));   // real, "ours" script
buf.extend(serialize(&real_outpoint));

let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted
assert_eq!(ro.offset(), Scalar::ZERO);         // offset never validated
// Wallet reports ro.value() as spendable balance; spending derives key K,
// producing a signature invalid against S -> funds reported, not spendable.
```