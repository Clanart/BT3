### Title
Secret scalar offsets leaked via derived `Debug` impls on key-carrying types — (File: `networks/bitcoin/src/wallet/mod.rs`, `crypto/dkg/src/lib.rs`)

### Summary
Analogous to CVE-2020-22783 (Etherpad storing passwords insecurely in the database and log files), several Serai types that hold secret scalar material derive `Debug`, causing the raw scalar values to be emitted whenever the value is formatted with `{:?}` — the exact sink log frameworks use. The rest of the codebase deliberately hand-implements `fmt::Debug` with `finish_non_exhaustive()` to *exclude* secret fields (`SecretShare`, `ThresholdCore`, `Encryption`, `SecretShareMachine`, `ThresholdView`), proving the intent that these scalars must never be printed. The derived impls on `ReceivedOutput`, `Scanner`, and `ThresholdKeys` bypass that protection.

### Finding Description
- `ReceivedOutput` derives `Debug` at `networks/bitcoin/src/wallet/mod.rs:89-97`. Its `offset: Scalar` field is the secret scalar offset which, added to the threshold secret share, yields the key able to spend the output — i.e., spend-critical secret material (`ReceivedOutput::offset`/`read`/`write` at lines 101-141 treat it as serialized secret state).
- `Scanner` derives `Debug` at `networks/bitcoin/src/wallet/mod.rs:152-156`; its `scripts: HashMap<ScriptBuf, Scalar>` maps every registered script to its secret offset. `register_offset` (lines 180-196) documents that offsets "must be securely generated" because an offset determines whether an arbitrary script path can spend the output.
- `ThresholdKeys` derives `Debug` at `crypto/dkg/src/lib.rs:291-301`. While `core` is `Arc<Zeroizing<ThresholdCore>>` whose manual `Debug` omits `secret_share` (lines 266-275), the derived impl still prints `scalar: C::F` and `offset: C::F` — the ephemeral secret tweaks applied by `scale`/`offset` (lines 400-417), including the TapTweak/negation applied in `tweak_keys` (`wallet/mod.rs:46-75`) and per-output HDKD offsets.

`k256::Scalar`'s `Debug` prints the scalar value, so `{:?}`/`{keys:?}`/`format!("{:?}", received_output)` — the formatting used by `log::debug!`/`tracing` call sites — discloses the offset bytes into logs. Note these types are *not* zeroized on drop either (`ReceivedOutput` has no `Zeroize`/`Drop`), so the secret persists in memory and in any serialized/log form.

### Impact Explanation
An unprivileged party who can read logs, error messages, or crash dumps containing a `Debug` rendering of `ReceivedOutput`, `Scanner`, or `ThresholdKeys` obtains the secret scalar offsets. For `ThresholdKeys`/TapTweak offsets this reveals the private tweak applied to the group key; for `Scanner`/`ReceivedOutput` it reveals the per-output offsets that define the spendable key path. Since `register_offset` warns that knowledge/control of offsets can introduce attacker-satisfiable script paths, disclosure of these offsets undermines the confidentiality assumptions of the HDKD scheme and can expose which offsets correspond to which outputs, aiding key-recovery or theft-of-funds analysis.

### Likelihood Explanation
Medium. `Debug` formatting of these structs is a single `{:?}` away in any caller (processor/signer code logs scan results and keys routinely — `processor/src/multisigs/scanner.rs`, `processor/src/signer.rs` contain numerous `log::` calls). Unlike Etherpad's unconditional password logging, exploitation requires a caller to actually debug-print the value and an attacker to obtain the output, hence Medium rather than High.

### Recommendation
Replace the derived `Debug` on `ReceivedOutput` (wallet/mod.rs:89), `Scanner` (wallet/mod.rs:152), and `ThresholdKeys` (crypto/dkg/src/lib.rs:291) with manual implementations that omit secret fields, matching the existing pattern (`SecretShare`, `ThresholdCore`, `SecretShareMachine`). For `Scanner`, print only `key`; for `ReceivedOutput`, print `output`/`outpoint`; for `ThresholdKeys`, use `finish_non_exhaustive()`. Additionally, implement `Zeroize`/`ZeroizeOnDrop` for `ReceivedOutput` and the `scripts` map so offsets are wiped after use.

### Proof of Concept
```rust
use networks::bitcoin::wallet::{Scanner, ReceivedOutput}; // bitcoin-serai
use frost::{curve::Secp256k1, ThresholdKeys};
use k256::Scalar;

// Scanner leaks every registered offset:
let scanner = Scanner::new(key).unwrap();
scanner.register_offset(secret_offset);
let log_line = format!("{scanner:?}"); // contains the Scalar offset bytes

// ReceivedOutput leaks its spend offset:
let dbg = format!("{received_output:?}"); // prints `offset: Scalar(0x...)`

// ThresholdKeys leaks scalar/offset tweaks (TapTweak, negation, HDKD offsets):
let keys = tweak_keys(keys);
let dbg = format!("{keys:?}"); // prints `scalar: 0x...`, `offset: 0x...`
```

**Uncertainty noted:** I verified the manual-secret-`Debug` pattern on `SecretShare`, `ThresholdCore`, `ThresholdView`, `Encryption`, and `SecretShareMachine`, and the derived `Debug` on `ReceivedOutput`/`Scanner`/`ThresholdKeys`. I did not exhaustively confirm that no `Zeroize`/`Drop` impl exists for `ReceivedOutput` elsewhere in `wallet/`, though none appears in `mod.rs`; the `Debug` leak stands regardless.