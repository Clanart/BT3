### Title
Forged `ReceivedOutput` deserialization is never re-validated against the chain before the FROST group signs a transaction over attacker-chosen prevouts - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class in the external report is "attacker-supplied destination reaches a sensitive sink with no validation" (SSRF: configured URL dialed verbatim). The Serai analog is that a `ReceivedOutput` — including its `outpoint`, `TxOut` value, and `offset` — can be fully fabricated via `ReceivedOutput::read` and fed into `SignableTransaction::new`, which then has the threshold group Schnorr-sign a Taproot sighash committing to those fabricated prevouts. The only check performed, in `SignableTransaction::multisig`, is that `prevouts[i].script_pubkey` equals `p2tr_script_buf(key + G*offset)`; the outpoint's existence, ownership, and on-chain value are never verified.

### Finding Description
`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) deserializes `offset` via `Secp256k1::read_F` and `output`/`outpoint` via `TxOut::consensus_decode` / `OutPoint::consensus_decode` with no consistency or authenticity checks — an untrusted byte stream can claim any outpoint and any satoshi value.

`SignableTransaction::new` (send.rs:150-256) consumes these `ReceivedOutput`s directly: `input_sat` is summed from the attacker-controlled `input.output.value` (line 175), `tx_ins` are built from the attacker-controlled `input.outpoint` (line 180), and `prevouts` is populated from the attacker-controlled `TxOut`s (line 253). Fee sufficiency, `NotEnoughFunds`, change computation, and dust checks all operate on the fabricated value.

`SignableTransaction::multisig` (send.rs:273-285) performs the sole validation: `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey`. This binds only the *script* to `key + G*offset` — trivially satisfiable by an attacker who sets the forged `TxOut.script_pubkey` to the Serai P2TR script. Nothing checks that `outpoint` references a real UTXO, that it pays to that script, or that the claimed `value` matches the chain.

`TransactionSignMachine::sign` (send.rs:373-397) then computes `taproot_key_spend_signature_hash(i, &Prevouts::All(&self.tx.prevouts), TapSighashType::Default)`, committing the signature to the forged prevout set, and every participant produces a FROST signature share over that message.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (an explicitly in-scope entry point) can:

1. Cause the threshold group to sign an unintended message — a sighash committing to prevouts the attacker invented. The resulting fully-signed transaction can never confirm (the committed prevout values/outpoints don't match a real UTXO, so the BIP-341 signature is invalid), so funds are not directly stolen; but the signed artifact exists and the signing session is consumed/griefed.
2. Report funds as received that are not spendable/valuable: `balance()`/`value()` on the forged `ReceivedOutput`, `fee()`, and the change/`input_sat` arithmetic all reflect the attacker's fabricated amount, not any real coin. An integrator crediting deposits or computing economics off `scan`-equivalent `ReceivedOutput` values will account for value that was never locked to the Serai key.
3. Manipulate fee/change math: an inflated `input_sat` silently converts the phantom surplus into `needed_fee`/change sizing (send.rs:224-234), distorting the transaction's economics before signing.

### Likelihood Explanation
Reachable by any party able to supply serialized `ReceivedOutput` bytes (e.g., a relayed or stored output consumed by the wallet path). Exploitation requires no validator privilege — only control of the deserialization input. The attack always produces either an invalid-but-signed transaction or misreported balances; it cannot produce a *valid* signature stealing funds, because the sighash commits to the fabricated prevouts and consensus will reject it. That bounds impact to Medium.

### Recommendation
- Bind `ReceivedOutput` to chain reality: when constructing `SignableTransaction`/`multisig`, verify each `prevout` by fetching `outpoint` and checking the on-chain `TxOut` equals the claimed script and value before computing sighashes.
- Authenticate `ReceivedOutput`s at the source: only accept outputs produced by `Scanner::scan_transaction`/`scan_block` (which derives `outpoint`/`value` from the actual transaction) rather than trusting `ReceivedOutput::read` on untrusted input, or add a documented integrity check (e.g., keyed MAC) for persisted outputs.
- Alternatively, commit only to the outpoint in the local check and derive `prevouts` from a trusted chain lookup rather than from the deserialized `TxOut`.

### Proof of Concept
Conceptual, on the real API surface (no network needed for the fabrication step):

```rust
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction, p2tr_script_buf};
use frost::curve::Secp256k1;
use k256::{Scalar, ProjectivePoint};

// group key (even-Y as required by tweak_keys/Scanner)
let key: ProjectivePoint = /* serai group key, even */ todo!();
let script = p2tr_script_buf(key).unwrap();

// Attacker serializes a forged ReceivedOutput:
//   offset = 0, output = TxOut { value: 21_000_000_0000_0000, script_pubkey: script },
//   outpoint = <any txid:vout, e.g. a real Serai deposit outpoint of dust value>
let forged_bytes: Vec<u8> = /* offset || consensus(TxOut) || consensus(OutPoint) */ todo!();
let forged = ReceivedOutput::read::<&[u8]>(&mut forged_bytes.as_ref()).unwrap();

// SignableTransaction::new accepts it: input_sat is the fabricated value,
// fee/change computed against phantom funds, no error.
let stx = SignableTransaction::new(
    vec![forged],
    &[(script.clone(), 100_000)],
    Some(script.clone()),
    None,
    10,
).unwrap();

// multisig() passes: it only checks p2tr_script_buf(key + G*offset) == prevout.script_pubkey,
// which the attacker set to the correct Serai script.
let machine = stx.multisig(&keys[&Participant::new(1).unwrap()]).unwrap();
// sign() then has every participant sign taproot_key_spend_signature_hash over
// Prevouts::All(forged prevouts) — a signed message committing to a UTXO/value
// that does not exist on chain. The aggregated BIP-340 signature is produced but
// the transaction can never confirm.
```

Concrete demonstration path: serialize a `ReceivedOutput` whose `output.value` is inflated (e.g. `u64::MAX/2`) and whose `outpoint` references a real dust deposit to the Serai address; `SignableTransaction::new` succeeds and reports `needed_fee`/`fee()`/change consistent with the phantom amount, and `multisig` returns `Some`, proving the forged prevouts reach the FROST sighash sink unvalidated.