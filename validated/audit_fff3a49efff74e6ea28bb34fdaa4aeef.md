### Title
`ReceivedOutput::read` accepts attacker-controlled `(offset, output, outpoint)` triples without verifying the offset actually derives the output's `script_pubkey`, allowing unspendable funds to be reported as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a missing-authentication bug: the `/cdp` relay endpoint accepted any loopback WebSocket client without checking it was the legitimate extension (no shared secret, no Origin check), letting an untrusted party drive a privileged interface. The analog in Serai's in-scope code is `ReceivedOutput::read`, the deserialization entry point for spendable Bitcoin outputs. It reads a scalar `offset`, a `TxOut`, and an `OutPoint` from untrusted bytes and returns a `ReceivedOutput` that claims `offset` is the HDKD offset needed to spend `output` — but it never authenticates that claim by checking `p2tr_script_buf(key + G*offset) == output.script_pubkey`. A `ReceivedOutput` produced by `Scanner::scan_transaction` is always well-formed by construction (the scanner builds the triple from a `script_pubkey -> offset` map it owns), but `read` is the path used when the bytes come from an untrusted source, and it performs no equivalent binding check.

### Finding Description
`ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs:122-134` deserializes three independent fields:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

There is no verification that `offset` is the offset which, applied to the scanner's key, yields `output.script_pubkey` — the only thing that makes the output spendable. By contrast, the legitimate construction path, `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`), only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns the offset registered for exactly that script. The read path skips this binding entirely, exactly as the relay accepted a connection without authenticating its origin.

The mismatch is only caught later, in `SignableTransaction::multisig` (`networks/bitcoin/src/wallet/send.rs:276-279`), where `p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey` causes `None` to be returned. Until that point, `ReceivedOutput::value()` (`mod.rs:116-118`) reports the attacker-chosen amount as received funds.

### Impact Explanation
An unprivileged party who can supply serialized bytes to `ReceivedOutput::read` (a reachable input per the scope rules) can fabricate a `ReceivedOutput` claiming arbitrary value — e.g., a real on-chain `TxOut`/`OutPoint` paying to an unrelated script, paired with an offset that does not derive that script for the multisig key. Downstream accounting that trusts `value()`/`output()` will report the funds as received, yet the output is unspendable by the threshold key: the signing machine refuses at `send.rs:277`, or worse, an integrator that skips `multisig`'s check path produces a transaction whose input signature is invalid on-chain. This matches the accepted impact class "funds reported received that are not spendable" — the read endpoint accepted unauthenticated data into a privileged (funds-representing) structure.

### Likelihood Explanation
Likelihood is moderate. Exploitation requires an attacker to control the byte stream deserialized via `ReceivedOutput::read` (e.g., outputs relayed between Serai services rather than produced locally by `Scanner`). No cryptographic break is needed — only the ability to write arbitrary `offset || TxOut || OutPoint` bytes, all of which are publicly-shaped data. The missing check is unconditional and deterministic; there is no probabilistic element. Severity is bounded to Medium because the inconsistency is eventually detected when spending is attempted (denial/inconsistency rather than direct theft), but any crediting decision made between `read` and `multisig` is corruptible.

### Recommendation
Authenticate the binding at deserialization or first use, mirroring how `Scanner` constructs the value. Concretely:

- Change `ReceivedOutput::read` to take the scanner's `key` (or require callers to pass it), and reject inputs where `p2tr_script_buf(key + G*offset) != Some(output.script_pubkey)`, the same check `SignableTransaction::multisig` performs at `send.rs:277`.
- Alternatively, make `ReceivedOutput` unconstructible from raw bytes except via `Scanner::scan_transaction`/`scan_block`, and have `read` re-derive membership via a `Scanner` instance.

### Proof of Concept
```rust
use networks_bitcoin::wallet::{ReceivedOutput, Scanner, p2tr_script_buf};
use bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, absolute::LockTime,
              transaction::Version, Transaction, TxIn, Sequence, Witness};
use k256::Scalar;
use std::io::Write;

// An honest scanner key; K is the multisig's tweaked group key.
let key = /* tweaked group key */ k256::ProjectivePoint::GENERATOR;
let mut scanner = Scanner::new(key).unwrap();
let offset = scanner.register_offset(Scalar::from(42u64)).unwrap();

// Attacker serializes a ReceivedOutput: real-looking outpoint + TxOut paying
// to an ARBITRARY script (e.g., the attacker's own address), with the
// multisig's registered offset attached.
let mut buf = vec![];
buf.write_all(&offset.to_bytes()).unwrap();
buf.write_all(&bitcoin::consensus::encode::serialize(&TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: ScriptBuf::new_p2tr_tweaked(
        /* attacker's own tweaked key, not key + G*offset */
        bitcoin::key::TweakedPublicKey::dangerous_assume_tweaked(
            bitcoin::key::XOnlyPublicKey::from_slice(&[2u8; 32]).unwrap()),
    ),
})).unwrap();
buf.write_all(&bitcoin::consensus::encode::serialize(
    &OutPoint::new(bitcoin::Txid::from_byte_array([0; 32]), 0))).unwrap();

let forged = ReceivedOutput::read(&mut &buf[..]).unwrap(); // accepted, no check
assert_eq!(forged.value(), 1_000_000);                    // reported as received

// But it is unspendable: at signing time the binding fails.
let signable = SignableTransaction::new(vec![forged], &payments, None, None, 1).unwrap();
assert!(signable.multisig(&tweaked_keys).is_none()); // send.rs:277 rejects
```

The bytes are accepted by `read` and report 1,000,000 sats received, yet no valid spend can ever be produced — demonstrating that the deserialized object's claimed binding (offset ↔ script) was never authenticated.