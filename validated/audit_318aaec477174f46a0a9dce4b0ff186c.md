### Title
Untrusted `ReceivedOutput` bytes poison the `Prevouts::All` sighash for every input, DoS-ing the whole Bitcoin signing session - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
Analogous to the Astaria H-17 class (an attacker injects a crafted object which later causes a critical protocol step to fail, locking funds for all honest participants), `SignableTransaction` / `TransactionSignMachine` in bitcoin-serai trusts the `prevouts` and `outpoint` fields of a deserialized `ReceivedOutput` without ever binding them to each other or to the actual UTXO set. Because the BIP-341 key-spend sighash is computed with `Prevouts::All(&self.tx.prevouts)`, a single malicious `ReceivedOutput` corrupts the sighash of **every** input, producing signatures that consensus rejects — burning the FROST signing session for all honest inputs.

### Finding Description
`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122`) deserializes three attacker-controlled, mutually independent fields:

- `offset` (a `Secp256k1` scalar via `read_F`)
- `output` (a `TxOut` via `consensus_decode`)
- `outpoint` (an `OutPoint` via `consensus_decode`)

Nothing ties `outpoint` to `output` — the reader just concatenates arbitrary bytes. `SignableTransaction::new` (`send.rs:150`) then:
1. Uses `input.outpoint` as the tx input (`send.rs:179-185`),
2. Uses `input.output` as the committed prevout (`send.rs:253`),
3. Sums `input.output.value` for the funds/change math (`send.rs:175`).

`multisig()` (`send.rs:273-284`) performs the only consistency check on the inputs: `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. It verifies the *script pubkey* matches the offset group key, but never verifies that the UTXO at `outpoint` actually exists, actually has that `script_pubkey`, or actually carries the claimed `value`.

Finally, `TransactionSignMachine::sign` (`send.rs:373-390`) computes each input's sighash with `Prevouts::All(&self.tx.prevouts)` — i.e., **every input's signature commits to every (claimed) prevout's script and value**.

Root cause: the in-scope crate validates the signing key/script correspondence but never validates prevout value or outpoint authenticity, while the Taproot sighash cryptographically commits to both.

### Impact Explanation
An unprivileged party who feeds crafted bytes to `ReceivedOutput::read` — exactly the untrusted-bytes entry point the scan rules admit — can produce a `ReceivedOutput` that passes `SignableTransaction::new` and `multisig()` but commits to false prevout data:

- `output.value` inflated: the change math (`send.rs:228-233`) creates a change output larger than real funds, and every signature commits to the fake amount.
- `outpoint` pointing at a nonexistent or foreign UTXO: the transaction spends an input that either doesn't exist or whose real `TxOut` differs from the committed prevout.

Since consensus recomputes the sighash over the *real* UTXOs, every produced signature fails verification on-chain — the transaction is permanently invalid. Worse, `Prevouts::All` means one poisoned `ReceivedOutput` invalidates the signatures of **all** honest inputs in the batch, mirroring H-17's "one malicious lien holder DoSes `_payment()` for everyone". The result is a failed signing session and an unbroadcastable transaction while the real funds remain locked behind a fresh signing round — a DoS / temporary fund-lock across all inputs, reachable purely from attacker-supplied bytes. (The shares produced remain valid Schnorr shares over the wrong message, so no key material leaks; the impact is liveness/fund-lock only.)

### Likelihood Explanation
The `ReadWrite`/deserialize → `SignableTransaction::new` → `multisig` → `sign` path requires no privilege: the attacker only needs to supply the serialized `ReceivedOutput` bytes (e.g., by constructing fake "received output" records that a downstream component ingests). The check that would catch it — comparing `prevouts[i]` against the real UTXO at `outpoint` — is absent by design in `multisig()`, which only compares `script_pubkey`. Deterministic, no race, no negligible-probability requirement: any mismatch in `value` or `outpoint` deterministically yields an invalid transaction. The ceiling is Medium: it is a DoS of the signing pipeline, not forgery or key recovery, and honest signers can retry.

### Recommendation
- When constructing a `SignableTransaction`, verify each `ReceivedOutput`'s claimed `output`/`value` against the actual prevout fetched for `outpoint` (or require the caller to supply verified prevouts), rather than trusting deserialized fields.
- At minimum, bind `ReceivedOutput::read` consumers to outputs produced by `Scanner::scan_transaction`/`scan_block` (which derive `outpoint` and `output` from the same on-chain object), and document that feeding externally supplied `ReceivedOutput`s is unsafe.
- Optionally, sign each input independently or use `Prevouts::One`-style commitments so a corrupt prevout only invalidates its own input's signature instead of poisoning the batch.

### Proof of Concept
```rust
// Attacker-controlled bytes -> ReceivedOutput::read
let mut buf = vec![];
buf.extend(Scalar::ZERO.to_bytes());                       // offset = 0
buf.extend(serialize(&TxOut {
    value: Amount::from_sat(1_000_000_000_000),            // fake, inflated value
    script_pubkey: p2tr_script_buf(group_key).unwrap(),    // passes multisig() check
}));
buf.extend(serialize(&OutPoint::default()));               // nonexistent/foreign UTXO
let fake = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

// Honest code path
let stx = SignableTransaction::new(
    vec![fake, honest_output], payments, Some(change), None, fee
).unwrap();                                                // succeeds
let machine = stx.multisig(&keys).unwrap();                // script check passes
// sign() computes sighash(i, Prevouts::All(&prevouts)) for i=0,1
// -> input 1's signature commits to the fake prevout too
let tx = complete(machine);
// broadcast: every input's sig fails -> tx rejected; signing session burned
```

Key lines: `ReceivedOutput::read` (`wallet/mod.rs:122-134`), `multisig()` check limited to `script_pubkey` (`send.rs:275-280`), `Prevouts::All` commitment (`send.rs:375-390`).