### Title
`ReceivedOutput::read` deserializes each field but never validates the offset actually unlocks the output's `script_pubkey` - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The PraisonAI report describes a "safe" wrapper that validates one field of an untrusted record (`member.name`, the claimed destination) while ignoring a second field (`member.linkname`, the actual destination), then performs the privileged operation anyway. The same shape exists in `ReceivedOutput::read`: it validates the *encoding* of each of three attacker-controlled fields (`offset`, `output`, `outpoint`) but never validates the *semantic binding* between them — namely that `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)`. The result is a `ReceivedOutput` that claims to be spendable with `offset` when it is not.

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) is the trusted construction path: it only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns the offset registered for that exact script, so by construction `output.script_pubkey` is the P2TR script for `key + G*offset` (see `register_offset` at lines 180-196 and `p2tr_script_buf` at lines 80-86).

`ReceivedOutput::read` (lines 122-134) bypasses that invariant entirely:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

Each field is checked for well-formedness (canonical scalar via `read_F`, consensus-valid `TxOut`/`OutPoint`), exactly like `_safe_extractall` checked `member.name` — but no check ties `offset` to `output.script_pubkey`, exactly like `linkname` going unchecked. Any consumer that trusts `output.offset()` to build a spending key (the signing path in `networks/bitcoin/src/wallet/send.rs` signs with the key tweaked by `output.offset()`) will produce a transaction whose signature does not correspond to the output's actual script public key.

Two distinct failure modes for crafted bytes `(offset', output, outpoint)`:

- `output.script_pubkey` is a registered script but `offset'` is a different registered offset (e.g., the `External`/zero offset vs the `Branch`/`Change`/`Forwarded` offsets registered in `processor/src/networks/bitcoin.rs:333-344`): the output is real and confirmed, but Serai will attempt to spend it with the wrong key, producing an invalid transaction — funds reported received that are not actually spendable as recorded.
- `offset'` is an arbitrary scalar: the output's key-path key is `key + G*offset_registered`, not `key + G*offset'`, so the signature never verifies; worse, if the attacker chooses `offset'` such that the record masquerades as a change/branch output, accounting treats the wrong output class.

The record also decouples `outpoint` from `output`, so a `ReceivedOutput` can pair a valid script-matched output body with an outpoint pointing at a different (possibly non-existent or already-spent) UTXO — again a field-vs-field binding that `read` never enforces.

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` (the prompt-designated untrusted sink) causes the wallet to accept a payment record whose claimed spending offset does not match the output's `script_pubkey`, or whose `outpoint` does not match the output body. Concretely: Serai reports/queues funds as received and spendable under `offset`, then the signing flow derives `key + offset` which does not control the output, yielding an unspendable/confused UTXO set — the accepted impact class "funds reported received that are not spendable." Severity Medium: the impact is confined to mis-accounted/mis-spent outputs rather than direct key compromise.

### Likelihood Explanation
Exploitation requires an attacker to control the byte stream passed to `ReceivedOutput::read` rather than merely broadcasting a transaction (the on-chain path via `scan_transaction` is safe because the script→offset binding is enforced there). Whether such attacker-controlled bytes reach `read` depends on where serialized `ReceivedOutput`s originate (local DB vs. coordinator/peer-supplied messages); I could not fully trace every `ReceivedOutput::read` caller in the available iterations, so reachability is the gating assumption. Where reachable, exploitation is deterministic — no races or probabilistic conditions.

### Recommendation
Add the missing second-field check, mirroring `filter="data"`: either (a) pass the scanning key into `ReceivedOutput::read` and reject records where `p2tr_script_buf(key + GENERATOR * offset) != Some(output.script_pubkey)`, or (b) reconstruct the record via `Scanner`-verified construction only, and document `read` as requiring prior verification. Additionally consider committing the `outpoint` to the same output so the tuple cannot be recombined.

### Proof of Concept
```rust
use bitcoin::TxOut;
use ciphersuite::Ciphersuite;
use ciphersuite_secp256k1::Secp256k1;
use serai_bitcoin::wallet::{ReceivedOutput, Scanner};
use bitcoin::hashes::Hash;
use bitcoin::{OutPoint, Txid, ScriptBuf, Amount};

// key: the scanner's group key (even-Y); offset A registered for script_A.
let mut scanner = Scanner::new(key).unwrap();
let offset_a = scanner.register_offset(a).unwrap();
let script_a = /* p2tr_script_buf(key + G*offset_a) */;

// Attacker crafts a record: real output paying script_a, but offset field set to
// the ZERO (External) offset so it's mis-classified / spent with the wrong key.
let mut buf = vec![];
buf.extend(Secp256k1::F::ZERO.to_repr().as_ref());            // offset' = 0
buf.extend(bitcoin::consensus::encode::serialize(&TxOut {
    value: Amount::from_sat(50_000),
    script_pubkey: script_a,                                 // really needs offset_a
}));
buf.extend(bitcoin::consensus::encode::serialize(
    &OutPoint { txid: Txid::all_zeros(), vout: 0 },
));

let rec = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted
// rec.offset() == 0, but spending script_a requires key + offset_a:
// any signature produced with key + rec.offset() cannot spend the UTXO.
```