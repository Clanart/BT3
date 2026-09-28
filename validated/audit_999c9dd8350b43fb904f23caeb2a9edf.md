### Title
`ReceivedOutput::read` performs no consistency validation between the claimed `outpoint`, `output` (TxOut), and `offset`, allowing an attacker to inject a `ReceivedOutput` that reports funds as received which are not actually spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class in the OlympusDAO exploit is *missing input validation on an attacker-supplied object*: `BondFixedExpiryTeller.redeem(token_, amount_)` trusted whatever contract address was passed in, calling `underlying()`/`expiry()`/`burn()` on a fake token and paying out real OHM. The Serai analog lives in `networks/bitcoin/src/wallet/mod.rs`: `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` from untrusted bytes with zero validation that they are mutually consistent or that they correspond to the scanning key. The trusted invariant — "`output` is the actual `TxOut` found on-chain at `outpoint`, and `offset` is the registered scalar satisfying `p2tr_script_buf(key + offset*G) == output.script_pubkey`" — is only true when the `Scanner` constructs the object itself. Nothing in `read` enforces it.

### Finding Description
`ReceivedOutput` has three fields — a scalar `offset`, a claimed `TxOut output`, and an `outpoint` — all read verbatim from the byte stream (`networks/bitcoin/src/wallet/mod.rs:122-134`). There is no check that:

1. `output.script_pubkey` equals `p2tr_script_buf(key + offset*G)` for any key (the reader has no key context and performs no check at all),
2. `outpoint` references a real UTXO whose `TxOut` equals `output`,
3. `offset` was one of the offsets registered via `Scanner::register_offset`.

By contrast, the legitimate construction path, `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`), only emits `ReceivedOutput`s whose `script_pubkey` is in the registered `scripts` map — the same invariant the Olympus teller failed to enforce on `token_`.

Downstream, `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) consumes `Vec<ReceivedOutput>` and uses `input.output` directly as the `prevouts` committed to by the Taproot sighash (`Prevouts::All(&self.tx.prevouts)` at `send.rs:375`) and to compute `fee()` (`send.rs:138-141`) and `input_sat` (`send.rs:175`). The only validation, in `multisig()` (`send.rs:273-285`), is `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — which a crafted `ReceivedOutput` trivially satisfies by copying the real P2TR script while pointing `outpoint` at a foreign/spent/nonexistent UTXO and inflating `output.value`.

### Impact Explanation
An attacker who can feed crafted bytes to `ReceivedOutput::read` (it is the deserialization entry point for persisted/passed outputs, e.g. via `Output::read` at `processor/src/networks/bitcoin.rs:156`) produces outputs whose `value()` reports arbitrary amounts as received. When spent via `SignableTransaction`, one of two bad outcomes occurs:

- If the claimed `output` does not match the actual on-chain UTXO at `outpoint`, the threshold signature is computed over a false `Prevouts::All` commitment, yielding a transaction that can never be valid on-chain — **funds reported received are not spendable**, and fee/change/dust math (`send.rs:175-235`) was computed against the fabricated value.
- The mismatch surfaces only at broadcast time, after the multisig has produced a signature, corrupting accounting (`fee()` returns a value inconsistent with the real input set).

Like Olympus, the contract of "this object is what the trusted component produced" is assumed rather than verified.

### Likelihood Explanation
Reachability is conditional: it requires an attacker-influenced `ReceivedOutput` byte stream to reach `read`/`SignableTransaction`. Outputs produced solely by `Scanner::scan_transaction`/`scan_block` are self-consistent, so this is exploitable wherever serialized `ReceivedOutput`s cross a trust boundary (database entries, network-provided blobs, `Output::read`). Because `read` is a listed untrusted-bytes entry point and the inconsistency is never checked anywhere in the pipeline before sighash commitment, this is a Medium-severity finding.

### Recommendation
- In `ReceivedOutput::read`, or in `SignableTransaction::new`/`multisig`, verify each input's `output.script_pubkey == p2tr_script_buf(key + offset*G)` *and* confirm `outpoint` resolves to the identical `TxOut` on-chain (or bind `output` to `outpoint` by consensus commitment rather than trusting both fields independently).
- Alternatively, store/derive `output` from chain data keyed by `outpoint` instead of deserializing it, eliminating the attacker-controlled `TxOut` entirely.

### Proof of Concept
```rust
// Attacker crafts bytes for ReceivedOutput::read:
//   offset: any registered/derivable scalar such that
//           p2tr_script_buf(victim_key + offset*G) == stolen_script
//   output: TxOut { value: 1_000_000_000 /* inflated */, script_pubkey: stolen_script }
//   outpoint: some real or fake OutPoint NOT containing that TxOut

let forged = ReceivedOutput::read(&mut crafted_bytes).unwrap();
assert_eq!(forged.value(), 1_000_000_000); // reported as received

// SignableTransaction::new accepts it; multisig() passes the
// script_pubkey check because the script genuinely matches key+offset.
let tx = SignableTransaction::new(vec![forged], &payments, None, None, fee).unwrap();
// Prevouts::All commits to the fabricated TxOut; the resulting threshold
// signature is invalid on-chain -> reported funds are unspendable.
```

Citations: `networks/bitcoin/src/wallet/mod.rs:120-149` (unvalidated `read`), `networks/bitcoin/src/wallet/mod.rs:199-214` (trusted construction path), `networks/bitcoin/src/wallet/send.rs:273-285` and `send.rs:373-390` (partial check + `Prevouts::All` sighash), `processor/src/networks/bitcoin.rs:145-166` (`Output::read` reaching `ReceivedOutput::read`).

Note: I could not fully trace every caller of `ReceivedOutput::read` within the iteration budget; the finding assumes the serialized form crosses a trust boundary, consistent with its inclusion in the untrusted-read surface.