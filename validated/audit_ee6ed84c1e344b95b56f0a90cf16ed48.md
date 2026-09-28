### Title
`SignableTransaction`/`TransactionMachine` sign over attacker-controlled prevout metadata without validating it against the chain - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The Bitcoin wallet's signing path trusts every field of each `ReceivedOutput` it is given: the `outpoint`, the `script_pubkey`, and critically the claimed `value`. `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) deserializes all of these from raw bytes with no on-chain validation. `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:175-191`) sums the claimed values to compute fees and change, and `TransactionSignMachine::sign` (`networks/bitcoin/src/wallet/send.rs:373-391`) commits to all of them via `Prevouts::All` in the BIP-341 sighash. The only check performed before signing, in `SignableTransaction::multisig` (`send.rs:273-285`), is that the claimed `script_pubkey` equals `p2tr_script_buf(key + G*offset)` — it never verifies the outpoint exists or that the claimed amount matches the real UTXO. This mirrors the Shido bug class (unvalidated input consumed by a privileged spending/authorization path).

### Finding Description
- `ReceivedOutput::read` accepts a scalar offset, an arbitrary `TxOut` (value + script), and an arbitrary `OutPoint` from untrusted bytes (`mod.rs:122-134`).
- `SignableTransaction::new` computes `input_sat` purely from `input.output.value` (`send.rs:175`) and uses it to decide `NotEnoughFunds` (`send.rs:215`) and the change amount (`send.rs:228-233`).
- `TransactionSignMachine::sign` builds `SighashCache` over `Prevouts::All(&self.tx.prevouts)` (`send.rs:373-386`), so every participant's signature commits to the fabricated prevout amounts and outpoints.
- `multisig` (`send.rs:276-281`) only confirms `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. An attacker need only provide a real (or even non-existent) outpoint whose claimed script equals a registered offset script — e.g., the base script registered in `Scanner::new` (`mod.rs:162-166`) with offset `ZERO`.

An unprivileged party who can feed a forged `ReceivedOutput` into the signing pipeline (malformed scanner/serialization input) can cause the threshold group to produce a signature over fabricated prevouts. Because BIP-341 commits to exact prevout amounts, any inflated value yields a transaction that consensus rejects — burning the signing session and stalling legitimate payments — while any *deflated* value still produces a signature covering a payment set the honest participants never audited against reality. Conversely, an output reported by `scan_transaction` (`mod.rs:199-214`) is credited solely by `script_pubkey` match with no confirmation the UTXO is mature or still unspent, so the scanner can report funds as received that are not actually spendable.

### Impact Explanation
The FROST multisig signs transactions binding to attacker-chosen prevout data. Forged `ReceivedOutput`s either (a) produce consensus-invalid transactions the network will reject (failed payouts, wasted fees, liveness loss for bridge withdrawals), or (b) allow crediting/spending decisions on amounts that do not exist, analogous to Shido's unauthorized claim of funds. This qualifies as "concrete signing of an unintended message" and "funds reported received that are not spendable".

### Likelihood Explanation
Reachable by an unprivileged party whenever `ReceivedOutput::read` or scanner output crosses a trust boundary (P2P message, DB record, or a Bitcoin transaction they crafted). No validator compromise, collusion, or leaked keys are required — only control over serialized bytes or of a transaction on the Bitcoin chain.

### Recommendation
Before constructing a `SignableTransaction`, verify each `ReceivedOutput`'s outpoint against a trusted Bitcoin node (`gettxout`/merkle proof), confirming the real value and script match the claimed `TxOut`. Reject coinbase/immature or spent outpoints at intake. Consider carrying a proof or confirmation height inside `ReceivedOutput` so `multisig`/`sign` cannot be fed fabricated prevouts.

### Proof of Concept
```rust
// Attacker-controlled bytes -> ReceivedOutput::read
let mut forged = vec![];
// offset = 0 (base script registered by Scanner::new)
forged.extend(Scalar::ZERO.to_bytes());
// TxOut: claim a huge value, script = serai p2tr script
TxOut { value: Amount::from_sat(1_000_000), script_pubkey: serai_script.clone() }
  .consensus_encode(&mut forged).unwrap();
// OutPoint pointing at any txid (need not exist or be ours)
OutPoint::new(Txid::all_zeros(), 0).consensus_encode(&mut forged).unwrap();

let ro = ReceivedOutput::read(&mut forged.as_slice()).unwrap();

// SignableTransaction::new accepts it; multisig() passes because the
// script_pubkey check matches p2tr_script_buf(group_key)
let stx = SignableTransaction::new(vec![ro], &payments, change, None, fee).unwrap();
let machine = stx.multisig(&keys).unwrap(); // passes script check
// sign() commits to Prevouts::All with the forged 1M-sat prevout;
// the resulting signed tx is consensus-invalid -> burned signing session.
```