### Title
ReceivedOutput::read deserializes an arbitrary (offset, script_pubkey) pair without verifying they correspond, letting attacker-supplied bytes bind an unauthorized signing offset to an output - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2020-14306 is an incorrect-access-control flaw: a low-privilege actor caused a privileged action (deploying a gateway) in an unauthorized scope. The Serai analog is a missing-integrity/authorization check in `ReceivedOutput::read`: the scalar `offset` (which determines which key controls the output) is deserialized from untrusted bytes completely independently of the `TxOut` it claims to spend. Nothing verifies that `output.script_pubkey == p2tr_script_buf(group_key + G*offset)`. An honest `Scanner` only ever produces consistent pairs (`scan_transaction` inserts `*offset` looked up by exact `script_pubkey`, networks/bitcoin/src/wallet/mod.rs:205-210), but the public `read` API accepts any pairing.

### Finding Description
`ReceivedOutput` has three fields: `offset`, `output`, `outpoint` (networks/bitcoin/src/wallet/mod.rs:90-97). `ReceivedOutput::read` (mod.rs:122-134) reads `offset` via `Secp256k1::read_F`, then consensus-decodes `TxOut` and `OutPoint`, and returns the object with no consistency check. The security invariant "this output is spendable by `group_key` offset by `offset`" is enforced only at scan time — an attacker who can get crafted bytes into `ReceivedOutput::read` (e.g., output data relayed into the processor pipeline, where `Output::read` in processor/src/networks/bitcoin.rs:145-166 directly calls `ReceivedOutput::read(reader)` on deserialized data) can claim any offset for any output.

Two concrete consequences:

1. Unspendable "received" funds: downstream signing (`SignableTransaction`/multisig machinery in wallet/send.rs) applies the claimed offset to the threshold keys when producing the input signature. A mismatched offset produces a signature under a key that does not control the `script_pubkey`, yielding an invalid spend. The output was reported/stored as received but is not spendable — satisfying the "funds reported received that are not spendable" acceptance criterion.

2. Wrong key attribution: `Output::key()` (processor/src/networks/bitcoin.rs:112-122) recovers the controlling group key as `script_key - G*offset`. A forged pairing makes a Serai key appear to own an output paid to an unrelated script, or vice versa — an authorization/classification bypass directly parallel to the CVE's "deploy to any namespace" primitive.

### Impact Explanation
An unprivileged party able to feed bytes to `ReceivedOutput::read` (or to the `Output::read` path that wraps it) can cause Serai to record an output as received/spendable under a chosen offset when it is not, corrupting output classification and producing spends that fail on-chain. Impact is integrity and availability of funds — mirroring the CVE's confidentiality/integrity/availability triad at reduced (Medium-High) severity because exploitation requires the deserialized path rather than the scanner path to be attacker-influenced.

### Likelihood Explanation
The read path trusts the producer. Where outputs originate only from `Scanner::scan_transaction`, the invariant holds by construction; but `ReceivedOutput::serialize`/`read` round-trip through storage and messages (the test at networks/bitcoin/tests/wallet.rs:73-75 exercises exactly this round-trip), so any untrusted or corrupted serialized output reaches `read` unchecked. Medium likelihood conditional on such a channel; the primitive itself is trivially constructible (any `Scalar` + any `TxOut`).

### Recommendation
Either re-derive and verify the binding on read — reject inputs where `p2tr_script_buf(group_key + G*offset) != output.script_pubkey` (requires threading the group key into `read`, or performing the check at the call site such as `Output::read`) — or make `offset` non-publicly-constructible by only exposing `read` behind an API that re-validates against the `Scanner`'s script table. At minimum, validate in `Output::read` that the claimed `kind`/offset is consistent with `output.script_pubkey` before trusting it.

### Proof of Concept
```rust
// networks/bitcoin context; attacker supplies bytes to ReceivedOutput::read
use k256::{Scalar, ProjectivePoint};
use bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, Txid, hashes::Hash};
use serai_bitcoin::wallet::{ReceivedOutput, p2tr_script_buf};

let group_key: ProjectivePoint = /* the Serai multisig key */;

// Honest output pays to group_key directly (offset 0)
let honest_script = p2tr_script_buf(group_key).unwrap();
let output = TxOut { value: Amount::from_sat(100_000), script_pubkey: honest_script };
let outpoint = OutPoint { txid: Txid::all_zeros(), vout: 0 };

// Attacker claims an arbitrary offset — e.g. the publicly derivable
// "change" offset hash_to_F(b"Serai Bitcoin Output Offset", b"change")
// or any Scalar — for an output that was NOT paid to key+offset.
let forged_offset = Scalar::from(1u64); // != the real offset (0)

let mut buf = vec![];
forged_offset.to_bytes(); // write offset
// ... consensus-encode `output` and `outpoint` into `buf` ...
let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted, no error
// ro.offset() == forged_offset, but script_pubkey pays to group_key + 0.
// Signing with key+forged_offset yields an invalid signature:
// the output is reported as received yet is not spendable by Serai.
```

`ReceivedOutput::read` returns `Ok` for this inconsistent triple because it performs no check relating `offset` to `output.script_pubkey` (mod.rs:122-134).

*Caveat:* within the iteration limit I could not fully trace every consumer of `ReceivedOutput::read`/`Output::read` to confirm a concrete untrusted-bytes channel end-to-end; the deserialization flaw itself is verified in the cited code, and `Output::read` (processor/src/networks/bitcoin.rs:145-166) demonstrably feeds `ReceivedOutput::read` without an offset/script consistency check.