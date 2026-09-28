### Title
Untrusted `ReceivedOutput` deserialization decouples the spent outpoint from the verified prevout script, letting an attacker report/spend "received" funds that are not owned or do not exist - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`ReceivedOutput::read` accepts an attacker-supplied `(offset, TxOut, OutPoint)` triple with no binding between the `OutPoint` (which determines what is actually spent on-chain) and the `TxOut` (which determines the reported value and the key used to sign). `SignableTransaction::multisig` only checks that the *claimed* `script_pubkey` matches `key + offset·G` — a value the attacker controls and can trivially satisfy — while the authoritative `previous_output` outpoint is never validated against anything. This mirrors the middie bug class: a security check is applied to one representation/field of the input (`script_pubkey` ↔ offset consistency) while the component that actually acts on the request (the sighash/`previous_output` spend) uses a different, attacker-controlled field.

### Finding Description
`ReceivedOutput::read` parses `offset`, `output` (`TxOut`), and `outpoint` (`OutPoint`) from raw bytes with only canonical-scalar and consensus-decode checks; nothing ties `outpoint` to `output` (send.rs-adjacent mod.rs:122-134). In `SignableTransaction::new`, the claimed `input.output.value` is summed into `input_sat` (send.rs:175) to justify payments and fees, and `input.outpoint` is embedded verbatim into `tx.input[i].previous_output` (send.rs:179-185). The only consistency check in `multisig` is `p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey` (send.rs:276-279) — it binds the *claimed script* to the *claimed offset*, not to the *outpoint* or the real UTXO. Signing then commits to `Prevouts::All(&self.tx.prevouts)` (send.rs:373-375), so the produced BIP-341 signature commits to attacker-fabricated prevout scripts and amounts.

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` can fabricate outputs with arbitrary value (inflating the multisig's reported balance — "funds reported received that are not spendable") and cause the coordinator to run a full FROST signing round over a transaction whose outpoints reference nonexistent or foreign UTXOs. The signed transaction commits to the fabricated prevouts, so it is rejected by consensus, burning a signing session and any retry/fee logic built on it. If the fabricated `script_pubkey` is set to `p2tr(key + offset·G)` for an attacker-chosen offset, the `multisig` guard passes despite the output not belonging to the multisig's real UTXO set, enabling repeated injection of phantom inputs into legitimate spends (fee misaccounting, forced `NotEnoughFunds`/`TooLowFee` failures, or invalid signed transactions).

### Likelihood Explanation
Reachability requires untrusted bytes reaching `ReceivedOutput::read` — an explicitly allowed attack surface per the engagement rules — but exploitation only yields fabricated accounting and invalid transactions rather than theft: any signature over false prevouts fails on-chain validation, and a phantom input in an otherwise valid spend makes the whole tx invalid. There is no secret leakage or forgery of a *valid* signature. Medium severity.

### Recommendation
Bind `ReceivedOutput` fields together at the trust boundary: either (a) have consumers re-derive `output` from chain data keyed by `outpoint` rather than trusting the serialized `TxOut`, or (b) in `SignableTransaction::new`/`multisig`, verify the serialized `ReceivedOutput` round-trips through `Scanner`-produced state, and document that `read` output must originate from the local scanner. At minimum, add a debug/documentation note that `read` performs no outpoint↔script binding.

### Proof of Concept
```rust
// Attacker crafts bytes for ReceivedOutput::read:
let offset = Scalar::ONE;                                  // arbitrary
let fake_txout = TxOut {
  value: Amount::from_sat(21_000_000_0000_0000),           // inflated claimed value
  script_pubkey: p2tr_script_buf(group_key + GENERATOR * offset).unwrap(), // passes multisig check
};
let fake_outpoint = OutPoint { txid: Txid::all_zeros(), vout: 0 }; // nonexistent UTXO
// After ReceivedOutput::read(...), SignableTransaction::new accepts it,
// input_sat includes 21M BTC, and multisig() returns Some(..) since the
// script↔offset check passes. The multisig signs a tx spending txid 00..00:0
// that consensus rejects — phantom balance counted, signing round consumed.
```