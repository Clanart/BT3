### Title
`ReceivedOutput::read` constructs spendable-output claims that bypass the `Scanner` script/outpoint guard, letting untrusted bytes inject forged UTXOs into `SignableTransaction` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog to the outPath/guard-bypass class: `Scanner` is the boundary guard for `ReceivedOutput` — it only emits outputs whose `script_pubkey` actually matches a registered offset key and whose `outpoint`/`value` come from a real on-chain transaction. `ReceivedOutput::read` reconstructs the same trusted type from raw bytes with no such guard, so an unprivileged party can inject `ReceivedOutput`s claiming arbitrary outpoints and values, which `SignableTransaction::new` then treats as real spendable inputs.

### Finding Description
`Scanner::scan_transaction` is the only honest producer of `ReceivedOutput`: it matches `output.script_pubkey` against the registered `scripts` map and stamps the real `txid`/`vout` (`mod.rs:199-214`). This is the "workspace guard" — it confines `ReceivedOutput` to outputs that exist on-chain and pay to keys derived from registered offsets.

`ReceivedOutput::read` (`mod.rs:122-134`) builds the identical type from a byte stream: an arbitrary scalar `offset`, a consensus-decoded `TxOut` (attacker-chosen `value` and `script_pubkey`), and an attacker-chosen `OutPoint`. No check ties the outpoint to a real transaction or the value to reality.

`SignableTransaction::new` (`send.rs:150-256`) then consumes these inputs: `input_sat` is summed from the claimed `output.value` (line 175), funding/dust/fee checks pass against fabricated value, `prevouts` are populated from the forged `TxOut`s (line 253), and `TransactionSignMachine::sign` produces FROST signature shares over `taproot_key_spend_signature_hash(i, Prevouts::All(&prevouts), Default)` (send.rs:373-390) — i.e., the threshold group signs sighashes committing to attacker-fabricated prevout amounts and outpoints.

`SignableTransaction::multisig` (send.rs:273-285) partially re-checks the script path (`p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`), but nothing validates the `outpoint` exists or that `value` matches the chain state — exactly the guard-bypass shape: a check exists for one field (script/offset binding) while the parallel attacker-supplied fields (outpoint, value) escape it.

### Impact Explanation
- The signing session produces shares over a transaction whose `Prevouts::All` commitment contains fabricated amounts/outpoints. Any resulting transaction is consensus-invalid (wrong committed amounts, or spending a nonexistent outpoint), so the threshold group "signs" a spend that can never confirm — funds treated as spent/received per the claimed inputs are not actually movable.
- With inflated `value`, `input_sat` and the change computation (`send.rs:228-233`) route real change based on phantom funds, corrupting accounting and burning a signing attempt/nonce allocation.
- With deflated `value`, a deliberately wrong sighash commitment can be used to probe/blame honest shares or sabotage a signing session from outside.

### Likelihood Explanation
Reachable by any unprivileged party able to feed bytes to `ReceivedOutput::read` (e.g., an integrator receiving outputs from a peer/processor rather than scanning blocks itself — the struct is public, `read`/`serialize`/`write` are public, and the type is explicitly designed to cross trust boundaries). No key material, validator status, or collusion is required. Impact is bounded to invalid transactions / wasted or sabotaged signing sessions and misreported funds — hence Medium, not High.

### Recommendation
Make the forged-input path impossible or explicit:
- Verify each claimed `outpoint`/`value` against the chain (or against a trusted scanner record) before `SignableTransaction::new`, or
- Restrict `ReceivedOutput` construction to `Scanner` (private fields already exist; remove/rename `read` to `read_unchecked` and document that callers must re-validate outpoint existence and amount), and have `SignableTransaction::new`/`multisig` re-derive legitimacy (e.g., check `scripts` membership) rather than trusting deserialized `output`/`outpoint`.

### Proof of Concept
```rust
// Attacker crafts bytes: real registered script_pubkey, inflated value, fake outpoint
let mut buf = Vec::new();
buf.extend(claimed_offset.to_bytes());           // offset whose key matches a registered script
buf.extend(serialize(&TxOut {
  value: Amount::from_sat(1_000_000_000),        // fabricated value
  script_pubkey: registered_p2tr_script,         // passes the multisig() script check
}));
buf.extend(serialize(&OutPoint::null()));        // nonexistent prevout
let forged = ReceivedOutput::read(&mut &buf[..]).unwrap();

// Integrator builds + signs
let stx = SignableTransaction::new(vec![forged], &payments, Some(change), None, fee_rate).unwrap();
let machine = stx.multisig(&keys).unwrap();      // script check passes
// ... FROST session signs sighash committing to Prevouts::All(fake TxOut) ...
// Resulting tx is consensus-invalid; the session and claimed funds are burned.
```

Note: I was unable to inspect `crypto/frost/src/sign.rs` internals (grep returned only a match count) to rule out a stronger analog in the FROST signing core; the finding above is the strongest reachable analog confirmed in the in-scope bitcoin wallet code.