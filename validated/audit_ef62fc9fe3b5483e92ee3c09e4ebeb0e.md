### Title
`ReceivedOutput::read` accepts arbitrary (offset, output) pairs without verifying the offset actually derives the output's `script_pubkey`, so any well-formed bytes "pass the interface check" - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary

The external report describes a probe function that accepts any target which "returns 32 bytes" — it validates a positive response without confirming the target actually supports the claimed interface, letting unrelated contracts pass validation. The structural analog in Serai is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`. The `Scanner` guarantees that every `ReceivedOutput` it produces satisfies the invariant `output.script_pubkey == p2tr(key + offset * G)`, but `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` from untrusted bytes with no such consistency check — the scanner's `key` is not even stored in the struct. Any attacker-supplied encoding that decodes is accepted as "an output spendable at this offset", exactly like a fallback function that happens to return 32 bytes passing `supportsInterface`.

### Finding Description

`Scanner::scan_transaction` builds `ReceivedOutput` only after confirming `self.scripts.get(&output.script_pubkey)` returns the offset registered for that script (`networks/bitcoin/src/wallet/mod.rs:205-211`), so honestly constructed values always satisfy `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey`.

`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`), however, reads:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

and performs zero validation tying `offset` to `output.script_pubkey`. The struct stores no group key, so the invariant enforced by `Scanner` is unverifiable — and unenforced — on the deserialization path. A second deviation mirrors the report's "missing negative check": because `key` is absent, there is no equivalent of the EIP-165 `0xffffffff` check (no way to prove the output does *not* belong to the registered script set). Consequences reachable by an unprivileged party feeding bytes to `ReceivedOutput::read`:

- **Unspendable credit**: a real on-chain `TxOut` (to an unrelated address) paired with any offset is reported as received at `value()` (`mod.rs:116-118`), yet the wallet holds no key that can spend it — "funds reported received that are not spendable".
- **Script-path injection**: `register_offset` documents that arbitrary offsets can introduce a spendable script path (`mod.rs:177-179`). `read` bypasses the secure-offset-registration discipline entirely; an attacker can pair an offset with an output whose script tree they control.
- **Fabricated outpoints**: `outpoint` is arbitrary attacker data; downstream code that trusts `outpoint()`/`value()` for accounting or scheduling can be fed phantom or duplicate deposits.

### Impact Explanation

Any component that deserializes `ReceivedOutput` from bytes an attacker can influence will treat attacker-fabricated outputs as spendable wallet funds. That yields misaccounted balances (outputs that can never be signed for, stalling or corrupting payment plans) and lets an attacker steer which "received" outputs downstream logic selects — analogous to unrelated contracts passing `requireInterface` validation because the probe never checked the negative case.

### Likelihood Explanation

The reachability hinges on a deserialization path feeding attacker-influenced bytes to `ReceivedOutput::read` (e.g., data relayed between scanner/processor components). Where that path exists the bug is deterministic — no cryptographic break needed, only well-formed encodings. Impact is bounded to accounting/spendability rather than direct key compromise, so Medium.

### Recommendation

- Store the scanner's group key in `ReceivedOutput` (or pass it to `read`) and verify `p2tr_script_buf(key + ProjectivePoint::GENERATOR * offset) == Some(output.script_pubkey)` on deserialization, rejecting mismatches — the equivalent of checking `supportsInterface(0xffffffff) == false`.
- Alternatively, make `ReceivedOutput` construction private to `Scanner`/`register_offset` and refuse deserialization of untrusted bytes, mirroring the recommendation to use a correctly-specified checker (the `ERC165Checker` analog) rather than a hand-rolled positive-only probe.

### Proof of Concept

```rust
use bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, Txid, hashes::Hash};
use k256::Scalar;
use bitcoin_serai::wallet::ReceivedOutput;
use std::io::Write;

// Craft bytes for an output paying to an arbitrary unrelated script,
// paired with any offset — read() accepts it as "received".
let mut buf = Vec::new();
buf.write_all(&Scalar::from(42u64).to_bytes()).unwrap();      // arbitrary offset
let txout = TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: ScriptBuf::new_op_return(&[0u8; 4]),        // not our P2TR script
};
buf.extend(bitcoin::consensus::encode::serialize(&txout));
buf.extend(bitcoin::consensus::encode::serialize(
    &OutPoint::new(Txid::all_zeros(), 0),
));

let recv = ReceivedOutput::read(&mut &buf[..]).unwrap();
assert_eq!(recv.value(), 100_000);   // reported as received...
// ...yet no registered key/offset can spend an OP_RETURN output.
```

`scan_transaction` would never produce this object (`mod.rs:205` requires the script to be registered), but `read` accepts it unconditionally, demonstrating the validation gap.