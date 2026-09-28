### Title
`ReceivedOutput::read` deserializes an unvalidated `offset`/`script_pubkey` pair, letting an attacker register outputs the multisig cannot actually spend - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class in the external report is a normalization step that silently transforms a benign input into a dangerous one (`.aassp` → `.asp`). In `bitcoin-serai`, the analogous gap is that `ReceivedOutput` couples two attacker-controlled fields — a scalar `offset` and a `TxOut` containing a `script_pubkey` — whose consistency is guaranteed when produced by `Scanner::scan_transaction` but is never re-verified on deserialization. `ReceivedOutput::read` accepts any `(offset, TxOut, outpoint)` triple, so a forged serialization can bind an arbitrary script to an arbitrary offset, producing an output that reports as received under the multisig key yet is not spendable (or is misattributed).

### Finding Description
`Scanner::scan_transaction` is the only honest constructor of `ReceivedOutput`, and it guarantees the invariant `output.script_pubkey == p2tr(key + offset * G)` by construction: it looks the script up in the `scripts` map it built itself (`networks/bitcoin/src/wallet/mod.rs:199-213`). However, `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reads the offset via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via consensus decoding, then returns the struct with no check that `p2tr_script_buf(key + offset * G)` equals the embedded `script_pubkey` — it doesn't even take `key` as a parameter.

Downstream, `Output::key()` in the processor recomputes the logical key as `script_key - offset * G` (`processor/src/networks/bitcoin.rs:112-122`), and the signing path derives the spend key as `group_key + offset` using `output.offset()`. If the serialized `offset` does not correspond to the script, the reported key is wrong and/or the multisig signs with a key that does not match the UTXO's taproot internal key, making the reported funds permanently unspendable.

### Impact Explanation
This matches the accepted impact class "funds reported received that are not spendable". An attacker who can feed untrusted bytes to `ReceivedOutput::read` (serialized `Plan`s/`Output`s exchanged between validator components — `Plan::read` at `processor/src/plan.rs:180-211` and `Output::read` at `processor/src/networks/bitcoin.rs:145-166` both transit through `ReceivedOutput::read`) can cause the processor to accept and schedule an input whose declared offset is inconsistent with its `script_pubkey`. Signing will then produce a BIP-340 signature under `group_key + offset` that does not correspond to the output's taproot key, so the transaction is invalid and the funds are burned from the protocol's perspective.

### Likelihood Explanation
Severity Medium. Exploitation requires the attacker to inject a malicious serialization into a channel that reaches `ReceivedOutput::read`; it cannot be done purely with an on-chain transaction (the on-chain path goes through `Scanner`, which enforces the invariant). No secret key material is required — only control of untrusted bytes, which the scoping rules explicitly list as in-scope for `ReceivedOutput::read`.

### Recommendation
Store the multisig `key` (or otherwise make it available) and, in `ReceivedOutput::read`, verify that `p2tr_script_buf(key + ProjectivePoint::GENERATOR * offset) == Some(output.script_pubkey)` before returning, rejecting any serialized triple that fails the check. Alternatively, serialize only `(outpoint, offset)` and re-derive/verify the `TxOut` against chain data.

### Proof of Concept
1. Let `K` be the multisig group key, `s` an arbitrary scalar the attacker picks, and `Q` an arbitrary taproot script the attacker controls (e.g., paying to their own key).
2. Serialize `ReceivedOutput { offset: s, output: TxOut{script_pubkey: Q, value: v}, outpoint: <any real outpoint> }`.
3. Feed the bytes to `ReceivedOutput::read` — it succeeds, since only canonicality of `s` and consensus validity of the `TxOut`/`OutPoint` are checked (`wallet/mod.rs:122-134`).
4. `output.key()` returns `x_only_key(Q) - s*G`, which need not equal `K`, or the signing code derives `K + s` while the UTXO pays to `Q`. Either way, a `SignableTransaction` built over this input produces a signature that does not satisfy the script — the reported funds are unspendable.

Note: I verified the invariant is enforced in `Scanner::scan_transaction` and absent in `ReceivedOutput::read`. The exact production call path that feeds attacker-controlled bytes into `ReceivedOutput::read` (rather than DB-trusted data) is the residual uncertainty; if all deserialization inputs are fully trusted, the practical severity drops.