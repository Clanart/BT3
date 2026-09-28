### Title
`SignableTransaction` grants input credit from attacker-declared `TxOut` values read via `ReceivedOutput::read`, which are never reconciled against the actual UTXO — (`networks/bitcoin/src/wallet/send.rs`)

### Summary

The Linux `smbdirect` bug is a resource-accounting error: recv credits were derived indirectly (counting posted buffers and inferred grants) instead of tracked by a dedicated counter, so the peer could be granted credits that never existed. The same shape exists in `bitcoin-serai`'s wallet: the "input credit" of a transaction being signed is taken from the `TxOut` embedded inside an attacker-supplied `ReceivedOutput`, while the only consistency check performed later is on the `script_pubkey` — never on the `value` or on whether the `outpoint` actually resolves to that `TxOut`.

### Finding Description

`ReceivedOutput::read` parses three attacker-controlled fields: a scalar `offset`, a full `TxOut` (script + value), and an `OutPoint` (txid + vout) (`networks/bitcoin/src/wallet/mod.rs:122-133`). No field is validated against chain state or against each other.

`SignableTransaction::new` then computes the input balance purely from the declared `TxOut`:

```rust
// networks/bitcoin/src/wallet/send.rs:175
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

and the solvency check `input_sat < payment_sat + needed_fee` (`send.rs:215`) is satisfied by the declared value, not the real one. The declared `TxOut` is also stored into `prevouts` (`send.rs:253`), which is later committed into the Taproot sighash via `Prevouts::All(&self.tx.prevouts)` (`send.rs:375`).

`multisig()` performs exactly one reconciliation (`send.rs:277`):

```rust
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey { None?; }
```

It checks only the script. The `value` field and the `outpoint`'s actual on-chain output are never verified — exactly the "credits granted which don't really exist" condition: the protocol grants itself input credit (`input_sat`, `fee()`, `needed_fee`) for value that was never proven to exist.

### Impact Explanation

Two concrete failures:

1. **Funds reported received/available that are not spendable.** An inflated declared `value` makes `NotEnoughFunds` pass and makes `fee()` (`send.rs:139-141`) and `needed_fee` computations treat phantom sats as real balance. Downstream accounting (change amounts, fee totals) is computed on non-existent credit.

2. **Signing of an unintended message.** The FROST participants' signature shares commit to `taproot_key_spend_signature_hash` over `Prevouts::All` containing the forged `TxOut` values (`send.rs:375-390`). Every signer produces a share for a sighash that does not correspond to any real spendable input, so the completed transaction's witnesses are invalid on-chain — the signing session's preprocesses/nonces are consumed to produce an unbroadcastable transaction, and the incorrect verifier-side accounting goes undetected until broadcast failure.

Reachability: `ReceivedOutput::read` is an explicitly in-scope untrusted-bytes entry point, and `SignableTransaction::new`/`multisig`/`TransactionSignMachine::sign` are the public sign path consuming it.

### Likelihood Explanation

Any input source that can feed crafted `ReceivedOutput` bytes (deserialized output claims, forwarded output records, or attacker-influenced outpoint descriptions) into `SignableTransaction::new` triggers this deterministically — no race, collusion, or key knowledge required. The inflated `TxOut` need only carry a `script_pubkey` matching `key + offset·G` to pass the sole check, which the attacker can compute since the group key and offset are public/known to them. Severity: Medium — integrity of balance accounting and a guaranteed-invalid signing round, without direct key-share leakage.

### Recommendation

- Treat the declared `TxOut` as a claim, not credit: `multisig()` (or construction time) should verify the `outpoint`'s real on-chain output equals `prevouts[i]` (value and script) before any `input_sat`/`needed_fee`/`fee()` accounting uses it — i.e., maintain the equivalent of a dedicated `credits.available` counter sourced from verified chain data, not from the peer-supplied record.
- Reject `ReceivedOutput` values whose `script_pubkey` doesn't match the offset key at `SignableTransaction::new` rather than deferring a partial check to `multisig`.

### Proof of Concept

```rust
// Attacker crafts bytes for ReceivedOutput::read:
//   offset   = any scalar o
//   TxOut    = { script_pubkey: p2tr_script_buf(group_key + o*G).unwrap(),
//                value: Amount::from_sat(1_000_000) }   // declared, not real
//   outpoint = real txid:vout of a 10_000-sat output (or a nonexistent one)
let forged = ReceivedOutput::read(&mut attacker_bytes).unwrap();

// SignableTransaction::new credits input_sat = 1_000_000 even though the real
// UTXO is worth 10_000; NotEnoughFunds is bypassed.
let tx = SignableTransaction::new(vec![forged], &payments, change, None, fee_rate).unwrap();

// multisig() passes: it only checks script_pubkey equality (send.rs:277),
// never the value or that the outpoint resolves to this TxOut.
let machine = tx.multisig(&keys).unwrap();

// sign() commits all honest signers' shares to a sighash over Prevouts::All
// containing the forged value (send.rs:375-390); the resulting signatures are
// invalid on-chain and the spent preprocesses are wasted.
```

Root cause: `prevouts` and `input_sat` are populated from unverified deserialized bytes (`mod.rs:122-133`, `send.rs:175`, `send.rs:253`), while the only validation (`send.rs:277`) covers the script and not the value — granting input credit that may not exist on-chain.