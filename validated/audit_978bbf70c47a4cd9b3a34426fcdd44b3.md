### Title
Deserialized `ReceivedOutput` reports attacker-chosen BTC value for a non-existent outpoint — funds are credited but can never be spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The fee-on-transfer bug class is "recorded balance > actual balance": `_fundPool` credits `amountAfterFee` while the strategy receives less. The Serai analog lives in `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`): it deserializes `offset`, a `TxOut`, and an `OutPoint` entirely from attacker-supplied bytes with no validation that the outpoint exists on-chain, that the referenced output's `script_pubkey`/`value` match, or that `offset` is consistent with the script. The `Scanner` produces authentic `ReceivedOutput`s, but anything fed through `read` (a peer-supplied or stored blob) is treated identically — the reported `value()` is whatever the attacker wrote.

### Finding Description
`ReceivedOutput::read` (`wallet/mod.rs:122-134`) trusts all three fields:

- `offset` is read via `Secp256k1::read_F` — any scalar is accepted.
- `output` (`TxOut` containing `value` and `script_pubkey`) is consensus-decoded — `value` can be up to the full field width and `script_pubkey` need not be one the scanner registered.
- `outpoint` is consensus-decoded — it may reference a nonexistent or already-spent UTXO.

Downstream, `value()` (`wallet/mod.rs:116-118`) returns `output.value` as the credited amount, and `SignableTransaction::new` (`wallet/send.rs:175-220`) sums these values into `input_sat` to authorize payments and change. The only integrity check is in `multisig` (`wallet/send.rs:276-279`), which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — i.e., it binds the *script* to the *offset*, but never verifies the outpoint resolves to a real, unspent output nor that the serialized `value` equals the on-chain amount.

### Impact Explanation
An attacker who supplies bytes to `ReceivedOutput::read` can fabricate a deposit: pick an arbitrary `OutPoint`, a `TxOut` whose `script_pubkey` matches a registered scanner script (`p2tr_script_buf(key)` is computable publicly from the group key), the correct `offset`, and an inflated `value`. The wallet/processor will report that BTC amount as received ("funds reported received that are not spendable" — the outpoint does not exist or the UTXO's real value is lower). Because `SignableTransaction::new` sizes payments and change off `input_sat`, the protocol may release or account for funds it does not hold; any spend attempt produces a signature whose `Prevouts::All` commitment (`wallet/send.rs:375-390`) does not match the real UTXO set, so the transaction is invalid — the discrepancy is discovered only after the value has already been credited.

### Likelihood Explanation
Medium. `ReceivedOutput::read` is a public deserialization entry point explicitly intended for passing outputs between components; any channel where one node feeds another its serialized outputs (restarts, coordination, peer messages) is a reachability path. Constructing the malicious blob requires only the public group key to pick a matching `script_pubkey`/`offset` pair.

### Recommendation
Bind a `ReceivedOutput` to chain reality at read/construction time: re-derive the `script_pubkey` from `offset` and the scanner's key and reject mismatches, and require proof (or an RPC lookup) that `outpoint` resolves to an unspent UTXO with exactly the claimed `value` and `script_pubkey` before the output is credited or used in `SignableTransaction::new`.

### Proof of Concept
Conceptual: with `Scanner::new(key)` and a registered `offset`, compute `script = p2tr_script_buf(key + G*offset)`. Serialize `ReceivedOutput { offset, output: TxOut { value: 1_000_000_000, script_pubkey: script }, outpoint: <fake> }`. Any reader accepting these bytes reports `value() == 1_000_000_000`; `SignableTransaction::new` will happily build payments against it, and `multisig`/`sign`/`complete` produce a transaction that consensus rejects because the prevout/amount commitment in `Prevouts::All` does not match the real UTXO set — the credited balance was never spendable.