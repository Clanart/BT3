### Title
Crafted `ReceivedOutput` (duplicate outpoint / forged prevout value) yields a signed but consensus-invalid transaction, leaving multisig funds permanently unspendable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The ZeroLend report exploits a second distribution path that moves the same reward balance without clearing the accounting record (`claimable`), effectively double-spending a balance. The Serai analog lives in the Bitcoin wallet: `SignableTransaction::new` treats each `ReceivedOutput` as a distinct, spendable balance without verifying that inputs are unique or that the claimed `TxOut`/outpoint is authentic. Because `ReceivedOutput::read` accepts fully attacker-controlled bytes (`offset`, `output`, `outpoint`), an attacker can feed two inputs sharing one outpoint — the on-chain equivalent of distributing the same balance twice without debiting it — or a real outpoint paired with a fabricated `TxOut` value. The multisig then produces a signature over a transaction Bitcoin consensus will never accept, while the plan is treated as completed.

### Finding Description
`ReceivedOutput::read` deserializes `offset`, `output` (a `TxOut`), and `outpoint` with no consistency checks (networks/bitcoin/src/wallet/mod.rs:122-134). `SignableTransaction::new` maps every supplied input into a `TxIn` and copies `input.output` verbatim into `prevouts` without checking for duplicate outpoints (networks/bitcoin/src/wallet/send.rs:175-185, 253). The only validation of the untrusted fields occurs in `SignableTransaction::multisig`, which checks solely that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — the script, not the value or the outpoint's existence/uniqueness (send.rs:276-279).

Two failure modes follow:

1. **Duplicate inputs (the direct analog of un-reset `claimable`).** Two `ReceivedOutput`s referencing the same `OutPoint` each contribute `output.value` to `input_sat`, inflating the apparent balance (send.rs:175) and passing `NotEnoughFunds` checks — just as `distributeEx` inflates a gauge's payout without decrementing `claimable`. The resulting transaction contains duplicate `TxIn`s and is unconditionally invalid under Bitcoin consensus. All `t` signers produce valid-looking shares, `TransactionSignatureMachine::complete` yields a "signed" transaction that any node rejects, yet the plan may be recorded as resolved.

2. **Forged prevout value.** A `ReceivedOutput` carrying a real outpoint and correct `script_pubkey` but an inflated `TxOut.value` also passes every check. Since `TransactionSignMachine::sign` commits `Prevouts::All(&self.tx.prevouts)` into the Taproot sighash (send.rs:375-390), the signature commits to a fictitious amount. The transaction broadcasts with signatures that fail BIP-341 validation, so it never confirms. Inflated values also corrupt `fee()`/change accounting (send.rs:139-141, 224-235), silently converting the phantom difference into fee or change.

### Impact Explanation
The multisig completes a signing session for a transaction that is permanently unbroadcastable/unconfirmable: the referenced funds remain on-chain but the plan/signing attempt is consumed, and any change derived from the fabricated values is wrong. This satisfies "funds reported received/spent that are not spendable" — the signature is produced over an unintended, invalid commitment, analogous to rewards being paid out while `claimable` still reports them available. Downstream retry logic cannot fix it because the inputs themselves are malformed.

### Likelihood Explanation
Any channel feeding untrusted bytes into `ReceivedOutput::read` — a defined untrusted-input sink — reaches `SignableTransaction::new`/`multisig` directly. Crafting a duplicate outpoint or inflated `TxOut` requires no cryptographic capability, only serialization of arbitrary field values. Triggering requires no collusion; a single corrupted input poisons the whole signing session.

### Recommendation
- In `SignableTransaction::new`, reject duplicate `outpoint`s across `inputs`.
- In `multisig` (or a dedicated `ReceivedOutput` validation step), verify the full `TxOut` (value and script) against the outpoint's confirmed on-chain output before signing, rather than trusting the deserialized `output` field.
- Consider making `ReceivedOutput` construction private to `Scanner` so the `offset`/`output`/`outpoint` triple is only produced by verified scanning, leaving `read` to round-trip only scanner-emitted bytes (still worth validating on load since DB contents feed `N::Output::read`).

### Proof of Concept
```rust
// networks/bitcoin: conceptual PoC
// 1. Take any real ReceivedOutput `o` scanned for the multisig key.
// 2. Craft duplicates/forgeries via the public read/write surface:
let mut forged = ReceivedOutput::read(&mut o.serialize().as_slice()).unwrap();
// Duplicate-input variant: same outpoint, same script_pubkey, passes multisig()
let dup = forged.clone();

// Forged-value variant: real outpoint + real script_pubkey, inflated value
// (field is private, so construct bytes: identical serialization with a larger
//  TxOut.value in the TxOut consensus encoding before the outpoint)
let tx = SignableTransaction::new(
    vec![forged.clone(), dup],            // or vec![o, forged_with_inflated_value]
    &[(payment_script, 10_000)],
    Some(change_script),
    None,
    10,
).unwrap();                               // succeeds: no dedup / no on-chain check

// All t signers run TransactionSignMachine::sign and produce shares.
// complete() returns a Transaction that:
//  - has two inputs spending the same OutPoint (consensus-invalid), or
//  - commits to a prevout amount the UTXO doesn't have (BIP-341 sighash invalid)
// => permanently unconfirmable while the plan is treated as completed.
```