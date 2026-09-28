### Title
`SignableTransaction::multisig` validates only the prevout `script_pubkey` while the produced signature commits to the full attacker-supplied prevout and outpoint, enabling signing of unintended spends — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The vm2 bug class is a check/use discrepancy: one component validates a normalized representation of a resource (a `path.resolve()`d path) while a different component acts on the real, dereferenced resource (Node's `require()` following the symlink). Serai's Bitcoin wallet has the same shape: `SignableTransaction::multisig` performs the access-control check (`isPathAllowed` analog) on only the `script_pubkey` of each prevout, but `TransactionSignMachine::sign` produces a BIP-341 signature committing to the *entire* `TxOut` (value + script_pubkey) and to the input's `previous_output` outpoint — fields that are never bound to the checked key or to each other.

### Finding Description
`SignableTransaction::multisig` (send.rs:273-285) is the only point where a signer validates that the transaction's inputs belong to the group key:

```rust
// networks/bitcoin/src/wallet/send.rs:276-279
let offset = keys.clone().offset(self.offsets[i]);
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
```

This checks only that the prevout's `script_pubkey` equals the P2TR script of the offset group key — the "resolved-path prefix check" analog. It does **not** verify:

1. That `self.tx.input[i].previous_output` (the outpoint) actually references the UTXO described by `self.prevouts[i]`. The two are populated independently from the attacker-influenced `ReceivedOutput` fields (`output` and `outpoint`, mod.rs:90-97) and are never cross-checked — they cannot be, since there is no chain access at this layer, but nothing even checks consistency or that the signer saw this exact pair before.
2. That `self.prevouts[i].value` matches the real on-chain value. The signature produced in `TransactionSignMachine::sign` commits to `Prevouts::All(&self.tx.prevouts)` (send.rs:373-386), i.e., to every claimed amount:

```rust
// networks/bitcoin/src/wallet/send.rs:383-390
let (sig, share) = sig.sign(
  commitments[i].clone(),
  cache
    .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
```

The untrusted bytes enter via `ReceivedOutput::read` (mod.rs:122-134), which deserializes `offset`, `output`, and `outpoint` from a raw byte stream with no validation beyond scalar canonicality and consensus decoding — an explicitly in-scope untrusted entry point.

Like vm2's check, the check operates on a "lossy" identity (script_pubkey, analogous to the unresolved path string) while the security-relevant operation (signature generation, analogous to `require()` dereferencing the symlink) acts on richer data that was never validated.

### Impact Explanation
Because the check verifies only key ownership while the signature commits to the full prevout set and outpoints, an attacker who can influence the `ReceivedOutput`s handed to a signer (they are untrusted bytes per the threat model — produced by `ReceivedOutput::read` / scanner pipeline data an external party can supply) can cause the threshold group to produce a valid Taproot key-spend signature for an input it never vetted: any existing UTXO paying to the same script_pubkey with the attacker-specified value becomes spendable under the group's signature, even if that UTXO was reserved for a different purpose, already partially handled, or misattributed. This is concrete "signing of an unintended message": the signer believes it authorized spending prevout A (the one the script check mentally refers to), while the signature is actually valid for the sighash over the attacker's substituted outpoint/prevout pair. If the attacker inflates `output.value` beyond the real UTXO, the resulting signature is invalid on-chain — meaning funds the scanner reported as received are rendered unspendable by that signing attempt while still being counted in `input_sat`, fee, and change math (send.rs:139-140, 228-230), which additionally distorts change outputs.

### Likelihood Explanation
Reachable by an unprivileged party: `ReceivedOutput` bytes are deserializable untrusted data (`ReceivedOutput::read`), and the whole `SignableTransaction` inputs vector is attacker-influenceable since deposits are initiated by external Bitcoin transactions scanned from the chain (`scan_transaction`, mod.rs:199-214) — an attacker controls which outpoint/TxOut pairs reach the signer by crafting deposit transactions and by controlling the data path feeding the wallet. No colluding threshold, malicious validator, or leaked key is required. Impact is bounded (misdirected/unspendable spends rather than key extraction), consistent with Medium.

### Recommendation
Bind the signature inputs to what was validated: in `SignableTransaction::multisig` (or `TransactionSignMachine::sign`), verify for each input `i` that `self.tx.input[i].previous_output` is the outpoint associated with `self.prevouts[i]` in the signer/`ReceivedOutput` source of truth (e.g., require the caller to present `(outpoint, txout)` pairs obtained from `Scanner::scan_transaction` and check membership), and check `prevouts[i].value` against the value reported by the scanning layer rather than trusting deserialized bytes. At minimum, document and enforce that `SignableTransaction::new`/`multisig` must only be called on `ReceivedOutput`s freshly produced by the local `Scanner`, and add a consistency assertion in `multisig` that each `TxIn.previous_output` was observed on-chain paying to the checked script_pubkey.

### Proof of Concept
```rust
// Attacker crafts bytes deserialized via ReceivedOutput::read:
//   offset   = legitimate offset so p2tr_script_buf(offset.group_key())
//              equals the group's script_pubkey (check passes)
//   output   = TxOut { value: V, script_pubkey: group_script }   // attacker-chosen V
//   outpoint = OutPoint of a DIFFERENT on-chain UTXO paying to group_script
//              (e.g., a UTXO reserved for another payout, with real value V)

// Signer side:
let input = ReceivedOutput::read(&mut attacker_bytes).unwrap();
let stx = SignableTransaction::new(vec![input], &payments, change, None, fee_rate).unwrap();

// multisig() check passes: script_pubkey matches offset.group_key()
let machine = stx.multisig(&keys).unwrap();
// ... FROST rounds ...
// sign() computes taproot_key_spend_signature_hash over attacker outpoint+prevout
// complete() yields a tx with a VALID BIP-340 signature spending the attacker's
// chosen UTXO — a spend the signer never intended to authorize.
```