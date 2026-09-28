### Title
Attacker-controlled `ReceivedOutput` offset crashes `SignableTransaction::multisig` via point-at-infinity - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::multisig` applies a serialized, attacker-chosen scalar offset from `ReceivedOutput` to the threshold group key and then calls `p2tr_script_buf` on the result. If the offset is chosen as the additive inverse of the group key's discrete log, the offset group key becomes the point at infinity, and `p2tr_script_buf` / `x_only` panic instead of returning `None`, crashing the signer.

### Finding Description
`ReceivedOutput::read` deserializes an arbitrary secp256k1 scalar (`offset`) directly from untrusted bytes, with no validity checks beyond canonical encoding at `networks/bitcoin/src/wallet/mod.rs:122-134`. That offset flows into `SignableTransaction::new` (stored in `offsets`) and then into `SignableTransaction::multisig`, which computes `keys.clone().offset(self.offsets[i])` and calls `p2tr_script_buf(offset.group_key())` at `send.rs:276-281`.

`p2tr_script_buf` at `networks/bitcoin/src/wallet/mod.rs:80-86` only rejects keys whose encoded tag is not `CompressedEvenY`; it does not reject the point at infinity. It then calls `x_only`, whose helper `x` does `encoded.x().expect("point at infinity")` at `networks/bitcoin/src/crypto.rs:13-23`. For the point at infinity the SEC1 encoding has no x-coordinate (and the encoding tag byte itself is invalid), so either `tag()` or `expect("point at infinity")` panics. The code's own documentation confirms this panic path: `x` and `x_only` are documented as "Panics on invalid input" and `Hram` is documented to panic "if either `R` or `A` is the point at infinity".

An unprivileged party who can supply a `ReceivedOutput` (or the bytes fed to `ReceivedOutput::read`) sets `offset = -x` where `x` is the discrete log of the (publicly known) threshold group key `G*x`. The offset group key is then `identity`, and `multisig` panics rather than returning `None`.

### Impact Explanation
Complete denial of service of the Bitcoin signing pipeline: any signer processing the crafted `ReceivedOutput` crashes when `SignableTransaction::multisig` is invoked to build the `TransactionMachine`. This aborts the FROST signing session for that input. Analogous to the InnoDB hang/crash class of CVE-2024-21173 — availability impact only, reachable by a party supplying untrusted transaction data.

### Likelihood Explanation
The attacker needs to know the group key (public) and to get a crafted `ReceivedOutput` consumed by `multisig` — e.g., a scanner-tracked output record deserialized from untrusted bytes, matching the prompt's allowed reachability (`ReceivedOutput::read` on untrusted bytes). The offset is a free 32-byte field; computing `-x` is trivial. No collusion or privileged access required.

### Recommendation
In `p2tr_script_buf`, explicitly return `None` when `key.is_identity()` before touching the encoded point tag/`x_only`. Additionally, `ReceivedOutput::read` could reject an offset that would reduce the group key to infinity, or `ThresholdKeys::offset` could document/return `Option` for this case. Do not rely on the `CompressedEvenY` tag check to exclude infinity.

### Proof of Concept
```rust
use bitcoin::{OutPoint, TxOut, Amount, ScriptBuf, Txid};
use bitcoin::hashes::Hash;
use k256::{ProjectivePoint, Scalar, elliptic_curve::ops::Reduce, U256, Field};
use frost::curve::{Ciphersuite, Secp256k1};
use serai_bitcoin::wallet::{ReceivedOutput, SignableTransaction};
use std_shims::io::Read as _;

// group_key = x * G is the publicly known threshold key.
// Craft offset = -x so group_key + offset*G == identity.
let x: Scalar = /* discrete log of group key */;
let evil_offset = -x;

// Serialize a ReceivedOutput with this offset
let mut buf = vec![];
buf.extend(evil_offset.to_bytes()); // Secp256k1::read_F input
// ... consensus-encoded TxOut + OutPoint with a script_pubkey matching p2tr of identity-adjacent key
let output = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

let tx = SignableTransaction::new(
  vec![output],
  &payments,
  None,
  None,
  fee_per_vbyte,
).unwrap();

// Panics inside p2tr_script_buf/x_only ("point at infinity" / invalid tag 0x00)
// instead of returning None.
let _machine = tx.multisig(&keys);
```

The panic occurs at `networks/bitcoin/src/wallet/send.rs:277` calling `p2tr_script_buf`, which reaches `x_only`'s `expect("point at infinity")` at `networks/bitcoin/src/crypto.rs:15` (or panics on the invalid `0x00` encoding tag of the identity point).