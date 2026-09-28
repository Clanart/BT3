### Title
Untrusted `ReceivedOutput` bytes are promoted into trusted spendable inputs without proving the outpoint exists - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` deserializes an attacker-controlled scalar offset, `TxOut`, and `OutPoint` into a type documented as “a spendable output,” but performs no consistency or provenance validation. A caller can therefore turn arbitrary bytes into a trusted output record carrying an arbitrary amount and claimed UTXO identity.

### Finding Description
`ReceivedOutput::read` independently calls `Secp256k1::read_F`, `TxOut::consensus_decode`, and `OutPoint::consensus_decode`, then returns `ReceivedOutput { offset, output, outpoint }` without checking that:

- the `output.script_pubkey` corresponds to the scanner’s key plus `offset`;
- the `outpoint` identifies a real confirmed transaction output;
- the claimed `output.value` is the value of that on-chain output; or
- the output was produced by `Scanner::scan_transaction` / `scan_block`.

The scanner path normally creates this trust relationship by matching `output.script_pubkey` against registered scripts and binding the result to `tx.compute_txid()` and `vout`. The deserializer bypasses those checks entirely.

`SignableTransaction::new` then trusts `input.output.value` as spendable input value and `input.outpoint` as the previous output. `SignableTransaction::multisig` checks only that `p2tr_script_buf(keys.offset(offset).group_key())` equals the supplied script; it does not and cannot establish that the claimed outpoint exists. If the attacker supplies the real group key’s P2TR script with offset zero, that check succeeds even for a fabricated outpoint.

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` bytes can cause nonexistent or incorrectly valued funds to be represented as spendable. If those bytes reach transaction construction/signing, Serai can produce and sign a Bitcoin transaction spending a fabricated UTXO. The transaction will be invalid on Bitcoin, while Serai-side accounting may have treated the forged input as received balance.

This is a trust-boundary violation: untrusted bytes become a “spendable output” without the chain-derived authentication that the scanner normally provides.

### Likelihood Explanation
The forged object is trivial to construct: a canonical scalar, any consensus-valid `TxOut`, and any consensus-valid `OutPoint` parse successfully. For the stronger signing path, the attacker needs only know the relevant public P2TR script and use offset zero, or a registered offset/script pair already derivable from public addresses. The attack does not require compromising validators, peers, RPC, or private keys. Its success depends on an integration accepting serialized `ReceivedOutput`/`Output` records from an untrusted source rather than reconstructing them via authenticated chain scanning.

### Recommendation
Do not treat deserialized `ReceivedOutput` values as authenticated spendable outputs. Either:

- remove/publicly restrict `ReceivedOutput::read` and construct `ReceivedOutput` only through `Scanner`;
- add an explicit validation API that checks `output.script_pubkey == p2tr_script_buf(scanner_key + offset)` and confirms `outpoint`, script, and value against a trusted chain view before use; or
- rename/split the API into a raw wire representation and an authenticated `ReceivedOutput`, so untrusted bytes cannot be confused with scanner-derived outputs.

Before scheduling or signing, `SignableTransaction` should receive only outputs reconstructed from confirmed blockchain data.

### Proof of Concept
```rust
// Conceptual PoC for networks/bitcoin/src/wallet/mod.rs and wallet/send.rs

let mut bytes = Vec::new();

// offset = 0
bytes.extend(Scalar::ZERO.to_bytes());

// Claim an arbitrary amount for the multisig's ordinary P2TR script.
let forged_txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: p2tr_script_buf(group_key).unwrap(),
};
bytes.extend(bitcoin::consensus::encode::serialize(&forged_txout));

// Claim an outpoint that was never created on-chain.
let forged_outpoint = OutPoint {
  txid: Txid::from_byte_array([0x42; 32]),
  vout: 0,
};
bytes.extend(bitcoin::consensus::encode::serialize(&forged_outpoint));

// This succeeds despite the outpoint not existing.
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000);

// Transaction construction trusts the claimed amount and outpoint.
let signable = SignableTransaction::new(
  vec![forged],
  &[(payment_script, 546)],
  Some(change_script),
  None,
  1,
).unwrap();

// The script check succeeds because the forged output uses the group's real
// P2TR script with offset zero; only the outpoint/value are fabricated.
let machine = signable.multisig(&threshold_keys).unwrap();
```

The resulting threshold signature commits to `Prevouts::All` containing the fabricated `TxOut`, producing a transaction for funds that were never actually received.