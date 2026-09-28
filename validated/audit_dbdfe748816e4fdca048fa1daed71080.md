### Title
`ReceivedOutput::read` accepts fabricated deposit records — attacker-chosen `offset`/`output`/`outpoint` let any bytes be registered as received multisig funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes an endpoint that resolves a privileged write target from caller-controlled parameters (`destination`, `scope`), letting an unprivileged user plant an object (an admin account) that the system then treats as legitimate. The Serai analog is the `ReceivedOutput` deserialization path in `bitcoin-serai`: `ReceivedOutput::read` constructs a "funds received by the multisig" record entirely from untrusted bytes — the scalar `offset`, the `TxOut` (value and `script_pubkey`), and the `OutPoint` — without any check that the triple was ever produced by `Scanner`, that the `offset` corresponds to a registered offset, or that the `script_pubkey` is actually the P2TR output of `key + offset*G`. Downstream, `SignableTransaction::new`/`multisig` trust the record's `value()` as multisig input, enabling fabricated deposits to be credited and signed spends built on them.

### Finding Description
`Scanner::scan_transaction` is the legitimate producer of `ReceivedOutput`s: it only yields outputs whose `script_pubkey` is in the `scripts` map (derived from the multisig key plus a registered offset), taken from an actual on-chain transaction (`networks/bitcoin/src/wallet/mod.rs:199-214`).

`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) is a parallel constructor reachable by untrusted bytes that bypasses all of that:

- `offset` is an arbitrary canonical scalar via `Secp256k1::read_F` — never checked against the scanner's registered offset set, and never even required to produce an even (usable) point.
- `output` is an arbitrary `TxOut` — the attacker chooses `value` (the credited amount) and `script_pubkey`.
- `outpoint` is an arbitrary `OutPoint` — it may reference a non-existent UTXO or a UTXO paying to a different script.

The only downstream consistency check is in `SignableTransaction::multisig` (`networks/bitcoin/src/wallet/send.rs:273-285`): `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey`. This is self-consistency of attacker-controlled fields, not a provenance check — the attacker simply sets `script_pubkey = p2tr(key + offset*G)` themselves for any `offset` they choose, since `register_offset`'s increment-to-even behavior (`networks/bitcoin/src/wallet/mod.rs:180-196`) means any scalar is a valid "offset" modulo parity. Nothing compares the claimed `outpoint`'s on-chain `script_pubkey` to the claimed `output.script_pubkey`, nor confirms the UTXO exists.

So bytes that never passed through the scanner — never observed on chain — deserialize into a record indistinguishable from a real deposit: a `ReceivedOutput` claiming arbitrary value at an arbitrary outpoint, with a self-consistent offset that passes the signing-time check.

### Impact Explanation
An unprivileged party who can feed bytes into `ReceivedOutput::read` can mint a deposit that was never received. If the consuming logic credits `output.value` as multisig funds (the entire purpose of the type — `value()` at `networks/bitcoin/src/wallet/mod.rs:116-118`, summed as `input_sat` in `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:175`), then:

- Fake deposits are credited as real balance (analogous to the planted `pwned.yaml` being treated as a real super-admin account: attacker-written bytes become privileged state).
- `multisig()` will produce a `TransactionMachine` that has the threshold set sign a transaction spending a nonexistent or attacker-irrelevant prevout — the signature is produced (signing of an attacker-constructed transaction), and the credited funds are not actually spendable on chain, yielding "funds reported received that are not spendable."

Depending on the surrounding system, this is direct theft of credited balance or persistent accounting corruption.

### Likelihood Explanation
`ReceivedOutput::read` + `serialize` are the canonical wire/persistence format for received outputs, so any flow that transmits or stores them across a trust boundary (relayers, message queues, DB rows populated from external data) exposes this. The primitive requires only control of the serialized bytes — no key material, no validator status, no collusion. The exploit primitive is deterministic: the attacker picks `offset`, computes `p2tr(key + offset*G)` (parity-adjusting `offset` as `register_offset` does), sets it as the claimed `script_pubkey`, and picks any `outpoint`/`value`.

### Recommendation
- Bind the record to the key: store the scanner `key` (or carry it as a parameter to `read`) and reject any `ReceivedOutput` where `p2tr_script_buf(key + offset*G) != output.script_pubkey` — and additionally verify `offset` is in the registered-offset set.
- Better: remove the free-form `read` constructor for untrusted input and require all `ReceivedOutput`s to originate from `Scanner::scan_transaction`/`scan_block`, which can only emit outputs actually present in a block.
- At spend time, verify each input's `outpoint` resolves to an on-chain UTXO whose `script_pubkey` and `value` match the claimed `TxOut` before crediting or signing.

### Proof of Concept
Given the multisig's tweaked group key `K` (public) and the ability to supply bytes to `ReceivedOutput::read`:

```rust
use k256::{Scalar, ProjectivePoint};
use bitcoin::{OutPoint, TxOut, Amount, Txid, hashes::Hash};
use bitcoin_serai::wallet::{ReceivedOutput, p2tr_script_buf};

// Attacker chooses any offset; increment until key + offset*G is even
// (identical to Scanner::register_offset's parity loop)
let mut offset = Scalar::ZERO;
let script = loop {
  if let Some(s) = p2tr_script_buf(K + ProjectivePoint::GENERATOR * offset) { break s }
  offset += Scalar::ONE;
};

// Fabricated "deposit": outpoint that doesn't exist (or pays to someone else),
// value = 1 BTC credited to the multisig
let mut buf = Vec::new();
buf.extend(offset.to_bytes());                                    // offset
buf.extend(bitcoin::consensus::encode::serialize(&TxOut {
  value: Amount::from_sat(100_000_000),
  script_pubkey: script,                                          // self-consistent
}));
buf.extend(bitcoin::consensus::encode::serialize(&OutPoint {
  txid: Txid::all_zeros(),                                        // nonexistent prevout
  vout: 0,
}));

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
assert_eq!(forged.value(), 100_000_000);

// SignableTransaction::new counts forged.value() in input_sat, and
// multisig(&keys) accepts it because script == p2tr(keys.offset(offset).group_key()).
// The signed spend is invalid on-chain; the credited 1 BTC was never received.
```

`ReceivedOutput::read` accepts this record with no error, and it survives the only consistency check in `multisig` (`send.rs:277`), demonstrating that untrusted bytes can fabricate a received-funds record that the wallet layer treats identically to scanner-observed deposits.