### Title
`ReceivedOutput::read` deserializes an unvalidated `offset`/`script_pubkey` binding — outputs reported spendable that the multisig key cannot spend - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the Linux `ims-pcu` flaw — where sysfs attribute handlers became reachable on interfaces lacking the state required to service them — `ReceivedOutput::read` reconstructs a `ReceivedOutput` (`offset`, `output`, `outpoint`) from untrusted bytes without enforcing the invariant that the producing path (`Scanner::scan_transaction`) guarantees: namely that `output.script_pubkey == p2tr(key + offset * G)` for the multisig key. Downstream code trusts this binding unconditionally, so a fabricated `ReceivedOutput` is treated as spendable when it is not.

### Finding Description
`ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` reads a scalar `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`. The only place the `offset` ↔ `script_pubkey` relationship is established is `Scanner::scan_transaction` (mod.rs:199-214), which derives the offset via a `scripts` HashMap lookup keyed on `script_pubkey`. The deserializer has no access to (and performs no check against) the scanner key, so any `(offset, script_pubkey, outpoint)` triple is accepted as a valid received output.

The trusted downstream consumers assume the Scanner-produced invariant:
- `SignableTransaction` / multisig signing uses `offset` to derive the spend key for the input; a mismatched offset yields a signature under the wrong key pair.
- `Output::key()` in `processor/src/networks/bitcoin.rs:112-122` reconstructs the group key as `script_pubkey_key − offset * G`, asserting only that the script is P2TR — it never verifies the reconstructed key is a registered multisig key.

### Impact Explanation
An unprivileged party that can cause untrusted bytes to be fed to `ReceivedOutput::read` (e.g., serialized output records relayed between components, or a malicious coordinator/peer supplying output data) can fabricate an output that is reported as received and queued for spending but is not actually spendable by the multisig: the declared `offset` either produces a key whose script differs from `script_pubkey`, or the `outpoint`/`TxOut` doesn't correspond to any real UTXO. This satisfies the "funds reported received that are not spendable" impact class: the processor can build and sign transactions spending a phantom input, producing invalid transactions, or mis-account balances and fee/change math in `SignableTransaction::new`. Depending on integration, a falsely-bound offset on a real UTXO could also steer signing toward an unintended key.

### Likelihood Explanation
Reachability is per the accepted threat surface: `ReceivedOutput::read` is a public deserialization entry point for untrusted bytes. Exploitation requires the attacker to influence the serialized `ReceivedOutput` consumed by a signer/scanner component rather than outputs discovered solely via `Scanner::scan_transaction` on-chain (which is self-consistent). Since the format is a fixed-layout serialization with no authentication and no consistency check, crafting such bytes is trivial; the constraint is whether the deployment trusts externally supplied `ReceivedOutput`s, which the library API surface permits. Severity: Medium.

### Recommendation
Bind the offset to the key at deserialization or first use: either take the expected group key (or `Scanner`) as a parameter to `ReceivedOutput::read` and verify `p2tr_script_buf(key + G * offset) == output.script_pubkey`, or add a `verify(&self, key: &ProjectivePoint) -> bool` method and require callers (e.g., `SignableTransaction::new`, `Output::key()`) to validate the binding before treating the output as spendable. At minimum, `Output::key()` should compare the reconstructed key against the registered multisig key set instead of asserting only the P2TR shape.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs exposes:
//   ReceivedOutput::read(r) -> { offset: Scalar, output: TxOut, outpoint: OutPoint }
// with no check that script_pubkey == p2tr(key + offset*G).

let mut bytes = Vec::new();
// Arbitrary offset not derivable from the multisig key for this script:
bytes.extend(Scalar::from(7u64).to_bytes());
// A TxOut paying to an unrelated P2TR script controlled by the attacker:
bytes.extend(serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: attacker_p2tr_script,
}));
bytes.extend(serialize(&OutPoint::new(real_txid, 0)));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted

// Downstream: SignableTransaction::new(vec![forged], ...) treats it as a
// spendable input. The produced signature uses key + 7*G, which does not
// control `attacker_p2tr_script` -> transaction is invalid / funds
// mis-accounted. No code path rejects the (offset, script) mismatch.
```