### Title
`ReceivedOutput` trusts a declared `TxOut` value that is never verified against the actual on-chain output, so the wallet reports and signs for funds that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the fee-on-transfer report — where the code trusts the *requested* `tokenAmount` instead of the *actually received* balance — `bitcoin-serai` trusts the *declared* `TxOut` inside a `ReceivedOutput` instead of the output actually present on chain. `ReceivedOutput::read` accepts an arbitrary `(offset, TxOut, OutPoint)` triple with no consistency check, and every downstream consumer (`value()`, `balance()`, `SignableTransaction::new`, `fee()`, the sighash `Prevouts::All` commitment) uses that self-reported value as ground truth.

### Finding Description
`ReceivedOutput::read` deserializes a scalar offset, a full `TxOut` (including `value`), and an `OutPoint` from untrusted bytes, with no binding between them — nothing proves the `TxOut` is the real output at that `OutPoint` (networks/bitcoin/src/wallet/mod.rs:122-134). When produced by `Scanner::scan_transaction` the fields are consistent because the scanner copies the real `tx.output[vout]` and computes the txid itself (mod.rs:199-213), but `read` is a public constructor for the same struct fed by untrusted bytes.

The declared value then propagates as fact:

- `ReceivedOutput::value()` returns `self.output.value.to_sat()` (mod.rs:116-118), and the processor reports this as externally received funds via `balance()` → `ExternalBalance { amount: Amount(self.output.value()) }` (processor/src/networks/bitcoin.rs:128-130).
- `SignableTransaction::new` sums `input.output.value` as `input_sat` for the solvency check and stores `inputs.map(|input| input.output)` as `prevouts` (send.rs:175, 253).
- `SignableTransaction::fee()` reports `sum(prevouts) - sum(outputs)` as the fee paid (send.rs:138-141).
- `multisig()` only verifies the *script_pubkey* equals the P2TR script of the offset group key — it never checks that the outpoint exists or that the value/script matches the real UTXO (send.rs:276-279).
- `sign()` commits the attacker-declared prevouts into the BIP-341 sighash via `Prevouts::All` (send.rs:373-390).

So a supplied `ReceivedOutput` with a fabricated or inflated `TxOut.value` is accepted, reported as a real received balance, and passed to FROST signing exactly as the original report's `tokenAmount` was passed to `mintAndStakeGlp` after receiving less.

### Impact Explanation
Two concrete impacts:

1. **Funds reported received that are not spendable.** A `ReceivedOutput` carrying a valid-`script_pubkey` `TxOut` but a fabricated `OutPoint`/value is surfaced by `balance()` as real BTC received. The accounting layer credits `output.value()` satoshis that do not exist on chain.
2. **Multisig signs a transaction that can never confirm.** `SignableTransaction::new` green-lights funding (`input_sat >= payments + fee`) against the phantom value, and `TransactionSignMachine::sign` produces FROST signature shares committing to the fake prevout via `Prevouts::All`. Under BIP-341 consensus the prevout amount is part of the sighash, so any mismatch with the real UTXO set makes the fully-signed transaction invalid — a burn of a signing round and a permanent invalid spend attempt, mirroring the original finding's "the transaction will fail" consequence.

### Likelihood Explanation
The vulnerable path requires untrusted bytes reaching `ReceivedOutput::read` (explicitly an in-scope untrusted input). Constructing the malicious input is trivial: any scalar offset (e.g., `Scalar::ZERO` for the base key), a `TxOut` whose `script_pubkey` is the P2TR script of the group key (so `multisig()`'s only check passes), and an arbitrary `OutPoint` plus attacker-chosen `value`. No key material or validator status is needed — only the ability to hand a serialized `ReceivedOutput` to a code path that treats it as scanned output.

### Recommendation
Mirror the "balance before/after" mitigation with a "reality check":

- Treat `ReceivedOutput::read` as untrusted-only and require proofs of provenance: fetch the real `TxOut` for `outpoint` from the chain/RPC (or include an SPV-style inclusion proof) and compare value + script_pubkey before use.
- At minimum, in `SignableTransaction::new`/`multisig()`, reject inputs whose `TxOut` cannot be confirmed against the referenced `OutPoint`, and have `scan_transaction` remain the sole trusted constructor by making `output`/`outpoint` fields private with no public read-based constructor for signing paths.

### Proof of Concept
```rust
// Attacker knows the multisig's even group key `key`.
let script = p2tr_script_buf(key).unwrap();
let forged = ReceivedOutput::read(&mut &{
    let mut buf = Scalar::ZERO.to_bytes().to_vec();      // offset = 0
    buf.extend(serialize(&TxOut {
        value: Amount::from_sat(1_000_000),              // claims 0.01 BTC
        script_pubkey: script,                           // passes multisig() check
    }));
    buf.extend(serialize(&OutPoint::default()));         // nonexistent UTXO
    buf
}[..]).unwrap();

// Wallet reports 1_000_000 sats received
assert_eq!(forged.value(), 1_000_000);

// SignableTransaction::new accepts it as funding; prevouts commit to the fake TxOut;
// every FROST participant signs a sighash over a prevout that does not exist ->
// the completed transaction is invalid and unspendable.
```
The declared `value` is used at mod.rs:117 (reporting), send.rs:175 (solvency), and send.rs:375 (sighash) without ever being checked against the actual chain state — the same root cause as trusting `tokenAmount` instead of the post-transfer balance.