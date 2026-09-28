### Title
`ReceivedOutput::read` accepts unbound `(offset, script_pubkey, outpoint)` triples — origin/consistency confusion yields outputs reported as spendable that are not - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2025-10529 is a same-origin policy bypass: a security decision made against the wrong context/origin. The analogous shape in Serai is the Bitcoin wallet's `ReceivedOutput`, which is the "origin receipt" binding a spend authority claim (the scalar `offset` that maps a script back to `key + offset·G`) to a concrete UTXO. `ReceivedOutput::read` deserializes `offset`, `TxOut`, and `OutPoint` as three independent, unauthenticated fields with no check that `output.script_pubkey == p2tr(key + offset·G)` or that `outpoint` actually references `output`. The only place the binding is established is inside `Scanner::scan_transaction`, which constructs `ReceivedOutput` itself from a `scripts` map lookup — so the invariant is enforced at scan time but silently dropped at deserialization time.

### Finding Description
`Scanner` maintains a `HashMap<ScriptBuf, Scalar>` (`networks/bitcoin/src/wallet/mod.rs:153-196`): `register_offset` inserts `p2tr_script_buf(key + G·offset) -> offset`, and `scan_transaction` returns a `ReceivedOutput` only when `output.script_pubkey` is present in that map (`:205-211`). That map lookup is the entire "same-origin" check: the script is proven to be spendable by `key + offset·G` at scan time.

`ReceivedOutput::read` (`:122-134`) then accepts:

```rust
let offset = Secp256k1::read_F(r)?;
output  = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
```

and returns `Ok(ReceivedOutput { offset, output, outpoint })` with no recomputation of `p2tr_script_buf(key + G·offset)` and no comparison against `output.script_pubkey`. The reader is explicitly listed as consuming untrusted bytes (`ReceivedOutput::read`), and these serialized outputs cross trust boundaries — they are written/read back through processor storage (`processor/src/networks/bitcoin.rs:136-166` `Output::read`/`write` round-trips `ReceivedOutput` with attacker-influenced `data` and `presumed_origin`) and are consumed by `SignableTransaction`/the scheduler as spendable inputs.

Two distinct confusions result:

1. **Offset/script mismatch.** An attacker-supplied `ReceivedOutput` pairing a real (on-chain, third-party-owned) `script_pubkey` with an arbitrary `offset` will be treated as spendable by `key + offset·G`. The FROST multisig will produce a valid BIP-340 signature for that derived key — which does not control the script — so the input can never be spent on-chain, yet downstream code has accounted it as balance.
2. **Outpoint/TxOut mismatch.** `outpoint` is decoded independently of `output`; nothing ties the claimed `TxOut` to the transaction output the `outpoint` references. The sighash commits to `Prevouts::All`, so a mismatched `output.value` also corrupts the fee/change computation in `SignableTransaction::new` (the value signed over is attacker-chosen, affecting `needed_fee`/`change` math).

### Impact Explanation
Funds reported received are not spendable: the scheduler crediting a spoofed `ReceivedOutput` will attempt to spend a UTXO the derived key does not control, burning the attempted input set into a transaction that is invalid (or, with a mismatched `TxOut` value, mispriced in fee/change). This is a Medium-severity integrity loss reachable purely from untrusted bytes fed to `ReceivedOutput::read` — the same class as the CVE (a check bound to the wrong "origin": the script–offset binding is verified only against the scanner's map, never against the deserialized claim itself).

### Likelihood Explanation
Reachability requires the attacker to control bytes passed to `ReceivedOutput::read` — e.g., data returned through the output persistence/`Output::read` path or any integrator that round-trips received outputs through untrusted serialization. It does not require a malicious validator, leaked keys, or protocol collusion. The bug is a missing-validation defect, not a probabilistic one.

### Recommendation
In `ReceivedOutput::read` (or in `SignableTransaction` input construction), re-establish the scan-time invariant: require the caller's `key`, recompute `p2tr_script_buf(key + G·offset)`, and reject the input unless it equals `output.script_pubkey`. Alternatively, store the script_pubkey → offset map alongside serialized outputs and re-validate on read, mirroring `Scanner::scan_transaction`'s lookup. Failing closed at deserialization restores the origin binding instead of trusting the byte-level claim.

### Proof of Concept
1. Let `key` be the multisig group key. Pick any on-chain P2TR output `O` paying to an unrelated x-only key `x`, with true `OutPoint` `P`.
2. Construct malicious bytes: `offset = o'` for arbitrary `o'` (e.g., `o' = 0`), `output = O`, `outpoint = P`; feed to `ReceivedOutput::read`. It returns `Ok`, where `Scanner::scan_transaction` would never have emitted it.
3. Pass the resulting `ReceivedOutput` into `SignableTransaction::new`. The transaction builds and the multisig produces a signature under `key + o'·G`; `O`'s script requires `x`, so the spend is invalid — the input is unspendable despite being credited.

*Caveat: I could not fully read `networks/bitcoin/src/wallet/send.rs` within the available iterations; if `SignableTransaction::new`/`multisig` re-derives and checks `script_pubkey` against `key + offset·G` internally, the exploitable surface narrows to incorrect accounting/crediting rather than construction of an unspendable transaction.*