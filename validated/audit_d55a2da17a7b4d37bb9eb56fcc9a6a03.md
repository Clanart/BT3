### Title
Untrusted `ReceivedOutput::read` accepts arbitrary `script_pubkey`/value/`outpoint` without binding them to the offset, letting an attacker report unspendable or fabricated received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the Streama bug — attacker-controlled bytes (URL + path) written to a sensitive sink without validation — `ReceivedOutput::read` deserializes an offset, a full `TxOut`, and an `OutPoint` entirely from untrusted bytes with no consistency check. The offset is never verified against the `script_pubkey`, and the `script_pubkey` is never required to be one the `Scanner`/wallet would recognize. The resulting object is treated as spendable balance by `SignableTransaction::new`.

### Finding Description
`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reads:
- `offset` via `Secp256k1::read_F` (canonicality only),
- `output` (`TxOut`) and `outpoint` via Bitcoin consensus decode (syntactic validity only).

No check binds `offset` to `output.script_pubkey` (i.e., that `p2tr_script_buf(group_key * offset)` equals the script), nor that the script is a P2TR output at all, nor that the outpoint exists. Compare with `Scanner::scan_transaction` (`mod.rs:199-214`), which only emits `ReceivedOutput`s whose `script_pubkey` is in the registered `scripts` map — the read path has no equivalent constraint.

The fabricated output is then consumed in `SignableTransaction::new` (`send.rs:150-256`): `input_sat` is summed from `input.output.value` (`send.rs:175`), so an attacker can inflate perceived balance with a `TxOut` claiming arbitrary value at an arbitrary outpoint. The only later check is in `multisig` (`send.rs:273-285`), which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — this rejects offset/script *mismatches*, but does nothing for an attacker who picks `offset = 0` (or a registered offset) with a fabricated script matching the wallet key, a fake outpoint, and an arbitrary value.

### Impact Explanation
"Funds reported received that are not spendable" / fabricated balance. An attacker who can feed serialized `ReceivedOutput` bytes (e.g., via a coordinator/peer relaying scan results, a database, or an RPC intake that reuses `ReceivedOutput::read`) can cause the wallet to:

1. Credit a received output of arbitrary value that does not exist on-chain (fake outpoint) — the wallet reports it as spendable balance.
2. Construct and drive the FROST threshold to sign a transaction spending a nonexistent prevout — the signed transaction is rejected by the Bitcoin network, burning a signing session and causing the wallet to believe funds moved/failed in inconsistent state.
3. Pair a real-looking script with a value larger than the actual on-chain output — the sighash commits to `Prevouts::All` (`send.rs:375`), so the inflated prevout value corrupts fee/change math (`send.rs:187-235`), potentially overpaying fees or mis-sizing change on otherwise valid inputs.

### Likelihood Explanation
Reachable by an unprivileged party anywhere serialized `ReceivedOutput`s transit trust boundaries — the struct exposes `serialize`/`write`/`read` precisely for persistence/transport. No key material or validator role is required; only the ability to supply bytes to `read`. Exploitation requires an integrator that trusts deserialized outputs without re-scanning them, which is the natural use of the API.

### Recommendation
In `ReceivedOutput::read`, or via a new `verify(&self, expected_scripts: &HashMap<ScriptBuf, Scalar>)`/constructor bound to a `Scanner`, require that `output.script_pubkey` is a registered P2TR script and that `p2tr_script_buf(scanner.key * offset) == output.script_pubkey`. At minimum, reject non-P2TR scripts and offsets that don't regenerate the script, mirroring the check already performed in `SignableTransaction::multisig` (`send.rs:277`).

### Proof of Concept
```rust
use bitcoin::{OutPoint, TxOut, Amount, ScriptBuf, Txid, hashes::Hash};
use k256::Scalar;
use serai_bitcoin::wallet::{Scanner, ReceivedOutput};
use frost::curve::Secp256k1;
use ciphersuite::Ciphersuite;

// Wallet group key -> scanner
let scanner = Scanner::new(group_key_point).unwrap();
let wallet_script = scanner_scripts_key; // p2tr_script_buf(group_key)

// Attacker crafts bytes: offset = 0, TxOut paying 21 BTC to the wallet's
// own script, at a nonexistent outpoint.
let mut buf = vec![];
buf.extend(Scalar::ZERO.to_bytes());
let fake_out = TxOut { value: Amount::from_sat(2_100_000_000), script_pubkey: wallet_script };
buf.extend(bitcoin::consensus::encode::serialize(&fake_out));
buf.extend(bitcoin::consensus::encode::serialize(
    &OutPoint::new(Txid::all_zeros(), 0),
));

let received = ReceivedOutput::read(&mut &buf[..]).unwrap();
assert_eq!(received.value(), 2_100_000_000); // 21 BTC "received", nothing on-chain

// SignableTransaction::new then counts 21 BTC as input_sat (send.rs:175),
// passes NotEnoughFunds, and multisig() accepts it because offset 0
// regenerates the wallet script — the threshold signs a tx spending a
// prevout that doesn't exist, which the network rejects.
```