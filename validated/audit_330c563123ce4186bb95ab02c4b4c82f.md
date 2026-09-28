### Title
Forged `ReceivedOutput` bytes are trusted as real balance, producing unspendable reported funds and wasted threshold signatures - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes an `offset`, a full `TxOut` (including a free-form `value` up to `u64::MAX`), and an arbitrary `OutPoint`, and the downstream spending path treats the result as a genuine, spendable UTXO. The only integrity check ever performed is that the stored `script_pubkey` matches the scanner's key/offset derivation (`SignableTransaction::multisig`); the claimed amount and outpoint are never validated against chain state. Any party able to feed bytes into `ReceivedOutput::read` can therefore cause the wallet/processor to report funds it cannot actually spend — the same bug class as the Monero malformed-ECDH-amount report, where a wallet credits an output that later cannot be swept.

### Finding Description
`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) reads:

- `offset` via `Secp256k1::read_F`
- `output` via `TxOut::consensus_decode` — arbitrary `value` and `script_pubkey`
- `outpoint` via `OutPoint::consensus_decode` — arbitrary txid/vout

There is no canonicality or sanity check on `value` (a `TxOut` may claim 2^64-1 sats, far beyond the 21M BTC supply) and no check that `outpoint` refers to a real UTXO.

When this object is later spent, `SignableTransaction::new` sums `input.output.value` into `input_sat` (send.rs:175) and `multisig` verifies only `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (send.rs:277-279). The script check passes for a forged output as long as the attacker sets `script_pubkey` to the victim's own P2TR script — which is fully public (`p2tr_script_buf(key)` plus the deterministic `hash_to_F(KEY_DST, "branch"|"change"|"forward")` offsets in processor/src/networks/bitcoin.rs:333-344, so even the Branch/Change/Forwarded scripts are publicly computable).

The result: `SignableTransaction::new` succeeds, `TransactionSignMachine::sign` drives a full FROST threshold signing session committing to `Prevouts::All` containing the forged `TxOut`s (send.rs:375, 386), and the produced transaction is invalid on-chain (nonexistent outpoint, or a prevout amount that does not match the real UTXO). The balance was reported as received/spendable but is not.

### Impact Explanation
An unprivileged party who can inject `ReceivedOutput` bytes (the accepted untrusted-input surface for this crate) causes the Serai processor to:

1. Book non-existent or inflated Bitcoin balance (value field is unbounded by the 21M supply cap or any reality check).
2. Consume a threshold signing session producing a signature over an attacker-influenced sighash (`Prevouts::All` commits to the forged amounts), yielding a transaction that can never confirm — permanently blocking that input set from sweeping real funds until the forged outputs are purged, mirroring the Monero "cannot sweep balance" impact.

### Likelihood Explanation
Reachability requires writing serialized outputs into the processor's output store/queue rather than merely sending on-chain transactions, which narrows the surface compared to a pure on-chain attack; however the read path performs no defense whatsoever, so a single forged blob suffices and no collusion or key material is needed. Medium likelihood, matching a Medium rating.

### Recommendation
- Reject `TxOut` values above the consensus maximum money supply in `ReceivedOutput::read`, and/or tag `ReceivedOutput` with the producing block/txid at scan time and verify `outpoint`/`value` against the block data when re-loaded, so persisted outputs cannot be forged or mutated.
- Fail `ReceivedOutput::read` (or `SignableTransaction::multisig`) when the script does not belong to the scanner's registered script set, so foreign/mutated blobs are dropped rather than scheduled for signing.

### Proof of Concept
1. Obtain the victim's group key `K` and compute `script = p2tr_script_buf(K).unwrap()` (public data).
2. Forge bytes: `Scalar::ZERO.to_bytes() || serialize(TxOut { value: Amount::from_sat(u64::MAX / 2), script_pubkey: script }) || serialize(OutPoint::new(fake_txid, 0))`.
3. Feed to `ReceivedOutput::read` → succeeds, producing a `ReceivedOutput` claiming ~92 quadrillion sats.
4. Pass it to `SignableTransaction::new(vec![forged], &payments, change, None, fee)` → `input_sat` is inflated, `NotEnoughFunds` never triggers; `multisig(&keys)` succeeds because the script check passes.
5. FROST signing completes; the resulting transaction commits to the forged prevout under `TapSighashType::Default` and is rejected by the Bitcoin network as spending a nonexistent/valor-mismatched UTXO — the reported balance is unspendable and the signing session was consumed.