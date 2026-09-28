### Title
`ReceivedOutput::read` accepts an offset/script_pubkey/outpoint tuple with no consistency check, so untrusted bytes can report funds as received that are not spendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Like the Tellor issue — where a price is consumed as authentic before any dispute/validation can occur — `ReceivedOutput` is a three-field assertion ("this `outpoint`/`TxOut` is spendable by `key + offset*G`") that is deserialized from raw bytes with zero validation. `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122`) reads `offset` via `Secp256k1::read_F`, then independently consensus-decodes a `TxOut` and an `OutPoint`. There is no check that `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey`, that the outpoint exists, or that the claimed `TxOut` matches the on-chain UTXO. A `ReceivedOutput` produced by `Scanner::scan_transaction` (`mod.rs:199-214`) is consistent by construction (the offset is pulled from the `scripts` map keyed by the actual `script_pubkey`); a `ReceivedOutput` produced by `read` from attacker-controlled bytes carries none of that guarantee.

### Finding Description
`ReceivedOutput::value()` (`mod.rs:116-118`) reports `output.value` unconditionally, and `SignableTransaction::new` (`send.rs:150-256`) consumes `inputs` trusting `input.output.value` for `input_sat`, fee accounting (`fee()` at `send.rs:139`), and the sighash via `Prevouts::All(&self.tx.prevouts)` (`send.rs:375`). The only binding check — `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey` — lives in `multisig` (`send.rs:273-285`), which merely returns `None` on mismatch *after* the malformed data was already accepted and accounted for. Nothing ever verifies the `outpoint`/`TxOut` pair against the chain, so an unprivileged party who can feed bytes into `ReceivedOutput::read` (e.g., outputs relayed between participants) can:

- Report a deposit (`value()`) for an outpoint that doesn't exist or whose script belongs to someone else — funds "received" that are unspendable, exactly the "consumed before it can be disputed" shape of the Tellor bug.
- Pair a real outpoint with a fabricated `TxOut` of inflated `value`. Since `SIGHASH_DEFAULT` commits to `Prevouts::All`, the FROST ceremony at `send.rs:373-397` will sign a message committing to the attacker's claimed prevout values — the group signs an unintended transaction whose implicit fee (`fee()`) is whatever the forged value dictates, and whose inputs were credited at amounts the chain never confirmed.
- Supply an `offset` that does not correspond to the `script_pubkey`, causing the output to be counted as wallet funds while `multisig` later fails — permanently wedging those funds in accounting or producing an invalid transaction.

### Impact Explanation
Two concrete harms reachable from public input bytes: (1) funds reported received that are not spendable — an attacker-credited `ReceivedOutput` inflates the wallet's balance and is used to fund payments (`NotEnoughFunds` check at `send.rs:215` passes against phantom value); (2) signing of an unintended message — the threshold signs a `taproot_key_spend_signature_hash` computed over attacker-supplied prevout values/outpoints, producing a transaction that is either invalid on-chain (burning the real inputs' opportunity and any real co-inputs) or commits to a fee the set never agreed to. Medium severity: requires the victim's code path to accept serialized `ReceivedOutput`s from an untrusted peer rather than only from its own `Scanner`, which is precisely the untrusted-bytes boundary this API exposes.

### Likelihood Explanation
`read`/`write`/`serialize` exist specifically for transporting `ReceivedOutput`s between parties/machines — otherwise serialization would be pointless. Any peer, coordinator, or external relayer supplying serialized outputs (e.g., "these are your deposits, sign the spend") hits this path. The missing check is trivial to exploit: write any `Scalar`, any `TxOut`, any `OutPoint`.

### Recommendation
- In `SignableTransaction::new` / `multisig`, or inside `ReceivedOutput::read` (taking the base key as a parameter), verify `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` for every input and reject mismatches, rather than deferring to the `None` return in `multisig`.
- Require callers to supply the chain-verified `TxOut` for each outpoint (or verify via the RPC layer) instead of trusting the serialized `TxOut`/`OutPoint` — treat `ReceivedOutput::read` output as a claim, not a fact, mirroring the "deviation check against independent sources" recommendation in the original report.

### Proof of Concept
```rust
// Attacker crafts bytes: any scalar offset, a TxOut claiming a huge value under an
// arbitrary script_pubkey, and an outpoint for a UTXO they control (or one that exists
// but isn't spendable by `key + offset*G`).
let mut buf = Vec::new();
buf.extend(Scalar::ONE.to_bytes());                       // bogus offset
buf.extend(serialize(&TxOut {
    value: Amount::from_sat(1_000_000_000),               // inflated value
    script_pubkey: p2tr_script_buf(victim_key).unwrap(),  // or attacker script
}));
buf.extend(serialize(&OutPoint::new(real_but_unrelated_txid, 0)));

let received = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted, no checks
assert_eq!(received.value(), 1_000_000_000); // phantom funds reported received

// Downstream, SignableTransaction::new counts it toward `input_sat`, and
// TransactionSignMachine::sign commits Prevouts::All to the forged TxOut,
// producing a threshold-signed transaction over attacker-chosen data.
```