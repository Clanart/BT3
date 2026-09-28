### Title
Untrusted `ReceivedOutput` bytes can bind an outpoint to a forged `TxOut`, producing signatures over wrong prevout data and unspendable "received" funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The audited bug class is a single logical quantity being used in two different representations: in `sponsorSeries`, the transferred amount was converted while the approved amount was not, so what was pulled in did not match what was authorized downstream. The Serai analog lives in `ReceivedOutput`: the value/script recorded for an outpoint is taken verbatim from deserialized bytes, while the spending path treats that same `TxOut` as both (a) the balance input for fee/payment math and (b) the committed `Prevouts::All` data in the BIP-341 sighash. There is no check that the `TxOut` matches the real output at `outpoint`. Like the original bug, two consumers that must agree on "the same" output — accounting/fee math vs. consensus-committed prevout data — are fed attacker-controlled values that need not match on-chain reality.

### Finding Description
`ReceivedOutput::read` deserializes three fields from untrusted bytes: a scalar `offset`, a `TxOut` (`output`), and an `OutPoint` (`outpoint`) — see `mod.rs:122-134`. Nothing binds `output` to `outpoint`; the `TxOut` is whatever the byte provider claims.

`SignableTransaction::new` then uses `input.output.value` for `input_sat` (the balance that must cover payments + fee, `send.rs:175,215`) and stores the same `TxOut`s as `self.prevouts` (`send.rs:253`). `TransactionSignMachine::sign` commits `Prevouts::All(&self.tx.prevouts)` into `taproot_key_spend_signature_hash` (`send.rs:375-386`). Under BIP-341, the sighash commits to each prevout's amount and script_pubkey, so the produced Schnorr signature is only valid if those match the real UTXOs.

The only consistency check is `multisig()` at `send.rs:273-285`, which verifies `p2tr_script_buf(keys.offset(offset_i).group_key()) == prevouts[i].script_pubkey`. An attacker crafting the bytes also controls `offset`, so they can make the claimed script match a registered wallet script — while still lying about `output.value` or pointing `outpoint` at a different real output.

### Impact Explanation
Two concrete outcomes, both reachable purely by feeding crafted bytes to `ReceivedOutput::read` (a listed untrusted-input sink):

1. **Funds reported received that are not spendable.** The wallet records a `ReceivedOutput` with an inflated `value` (or an `outpoint` whose real output belongs to a different script). `SignableTransaction::new` happily accounts `input_sat` using the fake value and emits a fully signed transaction — but the sighash commits to the fake prevout amount/script, so every node rejects it. The wallet believes it holds and spent funds it cannot actually move; a "successful" signing round produces a consensus-invalid transaction.

2. **Wrong fee/payment accounting.** Since `input_sat` derives from the forged value, `NotEnoughFunds` checks (`send.rs:215`) and change computation (`send.rs:228-233`) operate on phantom balance, mirroring the original bug's mismatched transfer/approve amounts.

### Likelihood Explanation
Requires an attacker to supply the serialized `ReceivedOutput` bytes — i.e., the wallet must consume outputs from an untrusted channel rather than only its own `Scanner` (which copies real `tx.output` values at `mod.rs:205-211`). Where bytes cross a trust boundary (e.g., outputs relayed between components or restored from untrusted storage), an unprivileged party controlling those bytes triggers this with no secrets. Impact is limited to incorrect accounting/unspendable transactions — no key material leaks — so Medium.

### Recommendation
Have `ReceivedOutput` verification re-derive the output from the referenced `outpoint` before use: when deserializing or before `SignableTransaction::new`, fetch the real UTXO (via RPC `gettxout`/`gettransaction`) and assert the `TxOut`'s `value` and `script_pubkey` equal the on-chain output. Alternatively, store only `(offset, outpoint)` and fetch the `TxOut` from a trusted source at spend time, ensuring the value used for fee math and the value committed in the sighash are the actual prevout values — the equivalent of the audit fix of using `stakeSize` consistently.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read:
// - real UTXO at outpoint P: { value: 10_000, script_pubkey: <wallet p2tr script S> }
// - forged ReceivedOutput:    { offset: 0 (or any offset whose tweaked key maps to S),
//                               output: TxOut { value: 1_000_000, script_pubkey: S },
//                               outpoint: P }
let forged = ReceivedOutput::read(&mut attacker_bytes).unwrap();
let tx = SignableTransaction::new(vec![forged], payments, change, None, fee_rate).unwrap();
// input_sat = 1_000_000 -> passes NotEnoughFunds; multisig() passes since script matches
let signed = /* full FROST signing round completes */;
// signed is consensus-invalid: BIP-341 sighash commits to prevout value 1_000_000,
// but the real UTXO is worth 10_000 -> every node rejects; wallet credited fake balance
```

Note: I could not verify every call site of `ReceivedOutput::read` within the available iterations (the index excerpts cover `crypto/`, `networks/bitcoin`, and parts of `processor/`). If the serialized `ReceivedOutput` never crosses a trust boundary in practice, this reduces to integrator-misuse and no valid analog exists; the strength of the finding hinges on those bytes being attacker-influenceable, which the task's own sink list (`ReceivedOutput::read`) presumes.