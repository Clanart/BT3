### Title
ReceivedOutput deserialization allows outpoint/TxOut mismatch; the script-only check in `SignableTransaction::multisig` lets attacker-claimed prevout values be committed into the Taproot sighash - (File: networks/bitcoin/src/wallet/mod.rs, networks/bitcoin/src/wallet/send.rs)

### Summary
This is a time-of-check-time-of-use / validation-scope analog to the referenced DNS-rebinding report: a security-relevant property is "checked" against one datum (the output's `script_pubkey`), while the data actually "used" at signing time is a different, attacker-influenced datum (the entire attacker-supplied `TxOut`, including its `value`, paired with an unrelated `outpoint`). An unprivileged party who can feed bytes to `ReceivedOutput::read` — or otherwise supply a `ReceivedOutput` whose fields are not internally consistent — can cause the signer to commit a BIP-341 signature over prevout data that does not match any real UTXO.

### Finding Description
`ReceivedOutput` is a triple of `(offset: Scalar, output: TxOut, outpoint: OutPoint)` with no invariant tying them together. `ReceivedOutput::read` deserializes each field independently and performs no check that `output` is the actual `TxOut` at `outpoint`, nor that `output.script_pubkey` corresponds to `key + offset*G` at all (networks/bitcoin/src/wallet/mod.rs:120-134).

At spend time, `SignableTransaction::new` splits this structure into three independent uses of the attacker-controlled fields:
- `tx.input[i].previous_output` is set from `input.outpoint` (send.rs:179-185),
- `prevouts[i]` is set from `input.output` (send.rs:253),
- `offsets[i]` is set from `input.offset` (send.rs:176),
- and `input_sat` (which decides `NotEnoughFunds`, change, and the effective fee) is computed from the attacker-claimed `input.output.value` (send.rs:175).

The only consistency check occurs later in `SignableTransaction::multisig`, which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (send.rs:273-281). This binds the *offset* to the *script*, but it never binds `prevouts[i]` to `tx.input[i].previous_output`. The signature message is then produced with `Prevouts::All(&self.tx.prevouts)` via `taproot_key_spend_signature_hash` (send.rs:373-390), committing to the full attacker-supplied `TxOut` (value and scriptPubKey) for each input position — while Bitcoin consensus will evaluate the signature against the *real* `TxOut` at that outpoint.

### Impact Explanation
The checked property (script ↔ offset) diverges from the used property (full prevout ↔ outpoint), exactly the check/use gap pattern of the reference bug:

- An attacker who supplies a `ReceivedOutput` whose `output.value` differs from the real UTXO's value causes every participant's FROST share to commit a sighash over a forged prevout amount. The aggregated Schnorr signature is then invalid on-chain: the transaction can never confirm, and the threshold signing session is burned. Because `input_sat` is computed from the same unverified `value`, an inflated claim can also push the transaction through `NotEnoughFunds`/change logic that would otherwise have failed, masking the corruption until the network rejects it.
- An attacker can point `outpoint` at a UTXO which does not pay to the multisig's script at all (the script check only compares `prevouts[i].script_pubkey` to `offset.group_key()`, not to the chain). The resulting signature again commits to a fake prevout and is unspendable.

Net effect: the wallet reports/acts on "received" inputs that are not actually spendable as described, and produces signatures over attacker-chosen prevout data that are guaranteed-invalid against the real chain state, halting spends of the multisig's real funds (availability loss with an integrity violation in the signed message itself).

### Likelihood Explanation
Exploitation requires an attacker to control the `ReceivedOutput`s passed into `SignableTransaction::new` — i.e., untrusted bytes reaching `ReceivedOutput::read` or a compromised/dishonest output-producer in the pipeline that constructs `SignableTransaction`s. The reachability model for this review explicitly includes untrusted bytes fed to `ReceivedOutput::read`. When `ReceivedOutput`s are produced exclusively by the local `Scanner::scan_transaction`/`scan_block` path, outpoint and output are internally consistent and the bug is not triggerable; the vulnerability exists in any deployment where outputs or their serializations transit a channel an attacker can influence (the struct is fully `Writable`/`read`-round-trippable and the serialization carries no authentication). The attack is deterministic once that precondition holds — no races or probabilistic elements.

### Recommendation
- Make `ReceivedOutput` enforce its internal invariant at construction/read time, or make the fields private behind a constructor that derives `output`/`outpoint` only from a verified scan (already true for `Scanner`, but `read` bypasses it).
- In `SignableTransaction::multisig`, in addition to the script check, re-derive and commit the outpoint→prevout binding: e.g., verify the transaction builds correctly by checking the signature against `Prevouts::All` before returning, or require callers to provide the confirmed `TxOut` fetched by outpoint rather than trusting the embedded `output`.
- Document that `ReceivedOutput::read` output must originate from a trusted scan and consider adding a checksum/MAC or inclusion proof to serialized outputs.

### Proof of Concept
```rust
// Conceptual PoC: craft a ReceivedOutput whose outpoint references a real UTXO
// (txid:vout paying to the multisig script with value V_real) but whose embedded
// TxOut claims a different value V_fake.

let real: ReceivedOutput = scanner.scan_transaction(&real_tx).swap_remove(0);
// real.output.value == V_real, real.outpoint == OutPoint{txid, vout}

let mut forged_bytes = vec![];
real.offset().write(&mut forged_bytes);          // same offset
// write an attacker TxOut: same script_pubkey (passes the multisig check), V_fake
TxOut { value: Amount::from_sat(V_fake), script_pubkey: real.output().script_pubkey.clone() }
    .consensus_encode(&mut forged_bytes).unwrap();
real.outpoint().consensus_encode(&mut forged_bytes).unwrap(); // same outpoint

let forged = ReceivedOutput::read(&mut forged_bytes.as_slice()).unwrap();
// forged passes `multisig()`: p2tr_script_buf(offset*G + key) == forged.output.script_pubkey

let stx = SignableTransaction::new(vec![forged], &payments, change, None, FEE).unwrap();
// sighash commits prevout.value = V_fake; Bitcoin will check against V_real
let tx = sign(&keys, &stx); // produces a fully-formed, permanently invalid transaction
```

Caveat: I verified the check/use divergence in `SignableTransaction::new`, `multisig`, and the `Prevouts::All` sighash commitment, and that `ReceivedOutput::read` performs no consistency validation. I did not exhaustively confirm every caller within scope boundary; if all `ReceivedOutput` producers are strictly local `Scanner` outputs over trusted RPC data, the practical severity drops (the trust boundary then sits at the Bitcoin RPC view, which is a documented assumption).