### Title
Forged `ReceivedOutput` via unauthenticated deserialization credits unsatisfiable "received" funds to the threshold wallet - (networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a `(offset, TxOut, OutPoint)` triple from arbitrary bytes with no authentication, no consistency check, and no on-chain existence check. A forged `ReceivedOutput` can claim an arbitrary outpoint holding an arbitrary amount paid to the multisig's own script. `SignableTransaction::new` then treats that forged object as a fully valid input: it sums the fabricated `output.value` into `input_sat` and feeds the object into FROST signing. `multisig` only verifies that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — a check the attacker trivially satisfies since the multisig script is public — so the threshold group produces valid BIP-340 signatures over a sighash committing to a nonexistent prevout.

This is the structural analog of the Anki iframe issue: the internal, privileged object (an "I received spendable funds" record, analogous to Anki's internal file-read API) is reachable through a channel (raw bytes into `read`) that bypasses the validation the trusted construction path (`Scanner::scan_transaction`, which only emits outputs actually present in a block) implicitly provides.

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) is the intended producer of `ReceivedOutput`: it only emits outputs whose `script_pubkey` is literally present in a confirmed transaction, guaranteeing the outpoint exists and the amount is real.

`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) is a parallel producer with none of those guarantees:

```rust
let offset = Secp256k1::read_F(r)?;
// TxOut (script_pubkey + value) and OutPoint (txid + vout) decoded raw
output = TxOut::consensus_decode(&mut buf_r)...
outpoint = OutPoint::consensus_decode(&mut buf_r)...
```

Nothing binds `offset` to `output.script_pubkey`, nothing proves `outpoint` exists, and nothing authenticates the value. Downstream:

- `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:175-176) sums `input.output.value` into `input_sat` and collects `input.offset` — trusting both.
- `SignableTransaction::multisig` (send.rs:273-282) performs the only "key ownership" check: `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. With `offset = Scalar::ZERO`, this is satisfied by simply copying the multisig's public P2TR script into the forged `TxOut`. The check validates the *script*, never the *existence* of the outpoint or the truthfulness of `value`.
- `TransactionSignMachine::sign` (send.rs:373-390) signs `Prevouts::All(&self.tx.prevouts)`, which commits to the fabricated `TxOut` value. The resulting transaction is a validly-signed transaction spending a nonexistent input.

### Impact Explanation
An `ReceivedOutput` deserialized from attacker-controlled bytes is indistinguishable from a genuinely scanned one to every consumer (`value()`, `output()`, `outpoint()`, fee math, signing). Concretely:

- Funds reported received that are not spendable: a forged record claims an arbitrary balance at an arbitrary outpoint. Any accounting layer trusting the object credits funds the threshold key cannot actually spend, inflating apparent wallet balance.
- Signature oracle framing for invalid spends: the forged input lets an attacker inflate `input_sat`, so `SignableTransaction::new` authorizes payments/change sized against phantom funds and the multisig signs the transaction. The signatures are cryptographically valid; the transaction is permanently unconfirmable because the prevout does not exist.
- Because `read`/`write`/`serialize` (mod.rs:136-148) round-trip exactly, a forged object also survives storage/persistence as a first-class `ReceivedOutput`.

### Likelihood Explanation
The bug is deterministic: `ReceivedOutput::read` performs no integrity or existence check and the only downstream guard (`multisig`'s script comparison) is satisfiable using purely public data (the group's P2TR script). Reachability requires untrusted bytes to reach `ReceivedOutput::read` — the same trust boundary the audit scope grants for `read_F`/`read_preprocess`-style entry points. In the in-repo trusted flow the Scanner constructs these objects itself, so the exploitability hinges on any path where serialized `ReceivedOutput`s cross a trust boundary (persistence, RPC, or peer-provided input lists); where that occurs, no malformed input is needed — any well-formed encoding succeeds.

### Recommendation
- Make `ReceivedOutput` construction forge-proof: either remove the public `read` deserializer, or re-scope it so a deserialized object is marked unverified until re-validated against chain data (e.g., a `verify(outpoint) -> TxOut` step that fetches the prevout and checks `script_pubkey`/`value` before the object may enter `SignableTransaction::new`).
- Bind the fields at read time: at minimum, assert `p2tr_script_buf(key + G*offset)` matches `output.script_pubkey` when the scanner key is in scope, so the offset cannot be detached from the output it claims to unlock.
- Never treat `value` as authenticated: derive the spendable amount from the on-chain prevout, not from the serialized `TxOut`.

### Proof of Concept
Conceptually, for a multisig with group key `K` (even-Y):

```rust
// Attacker crafts bytes; K's script is public
let script = p2tr_script_buf(K).unwrap();
let forged = ReceivedOutput {
    offset: Scalar::ZERO,                                   // passes multisig() check
    output: TxOut { value: Amount::from_sat(1_000_000_000), // phantom 10 BTC
                    script_pubkey: script },
    outpoint: OutPoint::new(Txid::all_zeros(), 0),          // nonexistent prevout
};
let bytes = forged.serialize();
let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted
// ro.value() == 1_000_000_000 credited to the wallet;
// SignableTransaction::new(vec![ro], payments, change, None, fee) succeeds
// and multisig() returns Some(...) — the group signs a tx spending a
// nonexistent outpoint. The funds "received" are unspendable.
```

The `multisig` gate in `networks/bitcoin/src/wallet/send.rs:277` passes because `offset.group_key()` equals `K`, whose script the attacker copied verbatim.