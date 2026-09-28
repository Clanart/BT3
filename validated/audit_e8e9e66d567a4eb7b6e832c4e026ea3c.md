### Title
`ReceivedOutput::read` accepts attacker-chosen scalar offset without binding it to the output's `script_pubkey`, yielding reported-received funds that can never be spent - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` deserializes three independent fields — a scalar `offset`, a `TxOut`, and an `OutPoint` — from untrusted bytes and performs no consistency check between them. In particular, it never verifies that `output.script_pubkey` equals `p2tr_script_buf(group_key + offset * G)`. A `ReceivedOutput` whose `offset` does not correspond to the key committed in the `script_pubkey` is accepted as a spendable received output, yet `SignableTransaction::multisig` can never produce a valid signature for it. This is the Serai analog of the Electrum incident's shape: an untrusted remote party supplies data that causes the wallet to treat funds as received/spendable when they are not — in Electrum the malicious server fed the wallet a fake "update" that redirected funds; here untrusted bytes fed to `ReceivedOutput::read` produce phantom, unspendable deposits.

### Finding Description
`ReceivedOutput` is the wallet's record of an owned UTXO. Its `read` constructor at `networks/bitcoin/src/wallet/mod.rs:122-134` reads `offset` via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via `consensus_decode`, with no cross-field validation:

```rust
// networks/bitcoin/src/wallet/mod.rs:122-134
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  ...
  output = TxOut::consensus_decode(&mut buf_r)...
  outpoint = OutPoint::consensus_decode(&mut buf_r)...
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

The only place the offset is reconciled with the script is much later, inside `SignableTransaction::multisig` at `networks/bitcoin/src/wallet/send.rs:275-282`, which re-keys the threshold keys by the supplied offset and compares `p2tr_script_buf(offset.group_key())` to `self.prevouts[i].script_pubkey`. A mismatch produces `None` — a silent signing failure — rather than a rejected deposit. By that point the output has already been stored and reported as received balance (`ReceivedOutput::value`, `mod.rs:116-118`), and every spend attempt fails at machine-construction time.

Additionally, the `prevouts` committed into the Taproot sighash (`Prevouts::All(&self.tx.prevouts)`, `send.rs:375`) take the `value` field from the same untrusted `TxOut`, so a forged `ReceivedOutput` with an inflated `output.value` would either make the transaction unrelayable or, if a matching outpoint exists, cause value-accounting errors in fee/change computation (`input_sat` at `send.rs:175` is summed from the untrusted field).

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` bytes (any path where outputs or descriptors are hydrated from untrusted input rather than produced by `Scanner::scan_transaction`, which derives offset and script together at `mod.rs:199-214`) can make the wallet report funds as received that are permanently unspendable: every signing attempt returns `None` from `multisig`, freezing the recorded balance. Inflated `value` fields also corrupt fee and change computation, either burning excess funds as fees or producing invalid transactions. This mirrors the Electrum incident's outcome — a victim's wallet state reflects funds that are not actually usable, induced entirely by attacker-supplied data.

### Likelihood Explanation
Exploitability depends on an integrator feeding attacker-controlled bytes to `ReceivedOutput::read`. Within the in-scope scope, `Scanner` itself produces consistent outputs, so the attack surface is any peer-supplied or stored output descriptor — a realistic condition for a wallet protocol that exchanges output records between unprivileged participants. No cryptographic break, collusion, or privileged access is required; only the ability to present crafted bytes to a `read` entry point. Medium likelihood, Medium severity.

### Recommendation
Bind the offset to the output at deserialization or first use:
- Add a validation method (e.g., `ReceivedOutput::verify(key)`) asserting `p2tr_script_buf(key + offset * G) == output.script_pubkey`, and require callers of `ReceivedOutput::read` to invoke it before treating the output as balance.
- Alternatively, store the key (not the raw scalar) and derive the script from it, or store the script's x-only key and reject reads where the offset-derived point mismatches.
- In `SignableTransaction::new`/`multisig`, distinguish "offset/script mismatch" from generic failure so forged inputs are rejected loudly rather than silently returning `None`.

### Proof of Concept
```rust
use bitcoin_serai::{
  bitcoin::{OutPoint, TxOut, Amount, ScriptBuf},
  wallet::{ReceivedOutput, SignableTransaction, p2tr_script_buf},
};
use k256::{Scalar, ProjectivePoint};
use frost::{curve::Secp256k1, Ciphersuite};

// Attacker constructs bytes: offset that does NOT match the script_pubkey key
let real_key = ProjectivePoint::GENERATOR * Scalar::from(7u64);
let script = p2tr_script_buf(real_key).unwrap(); // even-key P2TR script

let mut bytes = Vec::new();
// forged offset: claims key + 9*G, but script commits to key + 7*G
bytes.extend(Scalar::from(9u64).to_repr());
bytes.extend(bitcoin::consensus::serialize(&TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: script.clone(),
}));
bytes.extend(bitcoin::consensus::serialize(&OutPoint::new(real_txid, 0)));

// Accepted without error — no offset/script consistency check
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 100_000); // reported as received balance

// Later: SignableTransaction::new accepts it (input_sat credited 100k sats);
// SignableTransaction::multisig returns None on every attempt because
// p2tr_script_buf(keys.offset(9).group_key()) != script_pubkey.
// The "received" 100k sats are permanently unspendable.
```
Root cause: `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` trusts an unbound `offset`; the only reconciliation (`send.rs:277`) happens too late and fails silently.