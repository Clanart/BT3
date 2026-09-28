### Title
`ReceivedOutput::read` accepts an attacker-chosen offset/outpoint pair with no proof the offset actually unlocks the output's `script_pubkey` — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The OpenMeetings bug is a confused-deserialization class: untrusted bytes select a "handler" (URL protocol) that is never restricted, letting the attacker make the system ingest a resource it shouldn't. The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`. A `ReceivedOutput` is the tuple `(offset, TxOut, OutPoint)` whose *only* security invariant — that `p2tr_script_buf(group_key + G·offset) == output.script_pubkey` — is established solely by `Scanner::scan_transaction`, which derives `offset` from the `scripts` map keyed by `script_pubkey`. `ReceivedOutput::read` deserializes all three fields straight from attacker bytes and performs **zero** consistency checking: no `script_pubkey`-vs-offset check, no check that the `outpoint` refers to a real confirmed UTXO, and no check that the output isn't an immature coinbase.

### Finding Description
`Scanner::scan_transaction` constructs `ReceivedOutput` only when `self.scripts.get(&output.script_pubkey)` returns an offset that was registered for exactly that script (wallet/mod.rs:199–214). `ReceivedOutput::read` (wallet/mod.rs:122–134) instead reads:

- `offset` via `Secp256k1::read_F` — any scalar,
- `output` via `TxOut::consensus_decode` — any value and any `script_pubkey`,
- `outpoint` via `OutPoint::consensus_decode` — any txid/vout, including nonexistent or immature coinbase outpoints (`scan_block` explicitly includes coinbase transactions, wallet/mod.rs:221–227).

No field cross-validation exists at the deserialization boundary. The only place the invariant is checked is much later, in `SignableTransaction::multisig` (send.rs:273–285), which returns `None` when `p2tr_script_buf(offset.group_key()) != prevouts[i].script_pubkey` — silently dropping the whole transaction rather than rejecting the bad input.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` can cause funds to be **reported received that are not spendable**:

1. Claim a real Serai-owned deposit outpoint but with a mismatched/forged `offset` — `input_sat` in `SignableTransaction::new` (send.rs:175) counts the output's full value toward spendable balance, yet `multisig` later fails the `script_pubkey` check, so the UTXO is treated as received/consumed while never being spendable. Repeated for all real inputs, the wallet's accounting reports balance it cannot move.
2. Claim an outpoint that doesn't exist or belongs to a third party — again counted in `input_sat`, contributing to fee/change math, producing transactions that can never confirm.
3. Claim a coinbase output: `scan_block` already ingests coinbase transactions without a maturity check (the doc comment even warns a post-processing pass is needed), so `read` inherits the same hole for directly-submitted bytes — immature coinbases are counted as immediately spendable.

This maps the report's "read arbitrary files via unvalidated protocol handler" onto "spend arbitrary outputs via unvalidated offset handler": the deserialization path fails to restrict *which* resource the bytes are allowed to name.

### Likelihood Explanation
Reachable wherever untrusted `ReceivedOutput` bytes cross a trust boundary (plan/scheduler addendum serialization paths deserialize network outputs; `Plan::read` in processor/src/plan.rs:186–188 iterates `N::Output::read` over attacker-length-prefixed input lists with no upper bound on the count). Exploitation requires no key material and no collusion — only control of the serialized input. Severity: Medium — the forged entries fail at `multisig` rather than producing theft, but they corrupt balance reporting and can brick spending flows for genuinely received funds.

### Recommendation
Perform the consistency check at the trust boundary. Either:

- Give `ReceivedOutput::read` the scanning key (or a `&Scanner`) and verify `p2tr_script_buf(key + G·offset) == output.script_pubkey` during deserialization, and/or
- Have `Scanner` expose a `verify(&ReceivedOutput)` method used by every consumer of deserialized `ReceivedOutput`s before the output is counted as received.

Additionally, enforce a bounded input count in `Plan::read` (the `u32` length is currently unbounded) and exclude coinbase outputs from `scan_block` (scan `block.txdata[1 ..]`) or tag maturity on the `ReceivedOutput`.

### Proof of Concept
```rust
// Forge a ReceivedOutput claiming a Serai UTXO with an offset that does
// not correspond to its script_pubkey.
let mut bytes = vec![];
// offset: arbitrary scalar, e.g. 1, which was never registered for this script
bytes.extend((Scalar::ONE).to_bytes());
// TxOut: the real on-chain Serai deposit (correct script_pubkey, real value)
bytes.extend(serialize(&real_txout));
// OutPoint: the real (or fabricated) outpoint
bytes.extend(serialize(&real_outpoint));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // succeeds

// Balance accounting counts forged.value() in full.
// SignableTransaction::new(vec![forged], ...) passes the NotEnoughFunds check,
// but SignableTransaction::multisig(&keys) returns None because
// p2tr_script_buf(keys.offset(1).group_key()) != real_txout.script_pubkey.
// Result: funds reported received that are not spendable.
```