### Title
`ReceivedOutput` deserialization trusts attacker-claimed ownership metadata (outpoint, TxOut, offset) without binding them, enabling fabricated "received" funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2026-9561 maps to a bug class where attacker-supplied identity/provenance metadata is trusted as authoritative instead of the verifiable source. In `bitcoin-serai`, `ReceivedOutput::read` accepts an attacker-controlled triple `(offset, TxOut, outpoint)` with no check that the claimed `outpoint` actually contains the claimed `output`, or that `output.script_pubkey` corresponds to the wallet key shifted by `offset`. Downstream, `SignableTransaction::new` and `SignableTransaction::multisig` treat these claimed fields as authoritative for balance accounting and input selection, and the only consistency check (`p2tr_script_buf(key + offset*G) == claimed script_pubkey`) is satisfiable by an attacker who picks a matching `offset`/`script_pubkey` pair for an outpoint they do not control.

### Finding Description
`ReceivedOutput::read` reads `offset` via `Secp256k1::read_F`, then blindly `consensus_decode`s a `TxOut` and an `OutPoint` (networks/bitcoin/src/wallet/mod.rs:122-134). No verification ties the three fields together or to chain state. `SignableTransaction::new` then sums `input.output.value` as real input balance and uses `input.outpoint` as the transaction input (send.rs:175-185), so a forged `ReceivedOutput` inflates the wallet's reported balance. `SignableTransaction::multisig` performs the sole sanity check — that `p2tr_script_buf(keys.offset(offset).group_key())` equals the *claimed* `prevouts[i].script_pubkey` (send.rs:276-279). Since the attacker authors the `TxOut`, they can set `script_pubkey = p2tr(key + offset·G)` for any chosen `offset`, while pointing `outpoint` at an arbitrary on-chain UTXO (or a nonexistent one). The check passes and the full FROST signing round proceeds via `TransactionSignMachine::sign` (send.rs:383-391), producing a `Transaction` that is invalid on the network.

### Impact Explanation
Analogous to spoofing `X-Forwarded-For` to misattribute authority, an unprivileged party feeds serialized bytes to `ReceivedOutput::read` that falsely claim funds were received by the multisig. Consequences: (a) funds are reported received that are not spendable — accepted impact per the rules; (b) phantom balance skews `NotEnoughFunds`/change/fee math (send.rs:215-234), potentially inflating the fee burned or suppressing change; (c) the entire threshold validator set is driven through preprocess/sign/complete for a transaction Bitcoin consensus will reject, a signing-round DoS repeatable per forged input. Integrity impact on availability of signing plus fabricated accounting matches Medium severity.

### Likelihood Explanation
Any integration path that transports `ReceivedOutput`s over an untrusted channel (serialized via `serialize`/`write`, restored via `read` — explicitly an in-scope reachable API) lets an attacker inject claims without keys, collusion, or protocol privilege. Exploitation requires only knowledge of the multisig's public key to compute matching `script_pubkey`s, which is public.

### Recommendation
Have `multisig`/`SignableTransaction::new` (or `ReceivedOutput` construction) cryptographically bind `outpoint` to `output`, e.g. require callers to supply the prevout proof or re-derive ownership from the scanner, and reject `ReceivedOutput`s whose `outpoint` was not produced by `Scanner::scan_transaction`/`scan_block`. At minimum, document that `read` yields unauthenticated claims and add a `verify(key, chain_lookup)` helper asserting `output` equals the on-chain content of `outpoint`.

### Proof of Concept
```rust
// Attacker knows the multisig's public `key` (public information).
let offset = Scalar::ONE; // any value
let claimed_script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * offset)).unwrap();

// Point at a real UTXO the attacker does NOT control (or a fake one).
let forged = ReceivedOutput::read(&mut &{
  let mut b = offset.to_bytes().to_vec();
  // TxOut { value: 1_000_000_000, script_pubkey: claimed_script }
  b.extend(serialize(&TxOut { value: Amount::from_sat(1_000_000_000), script_pubkey: claimed_script }));
  b.extend(serialize(&victim_outpoint)); // arbitrary OutPoint
  b
}[..]).unwrap();

// Wallet accepts fabricated balance; the only check passes because
// script_pubkey was built as p2tr(key + offset*G).
let tx = SignableTransaction::new(vec![forged], &payments, Some(change), None, fee)?;
assert!(tx.multisig(&keys).is_some()); // passes; resulting tx is invalid on-chain
```