### Title
`ReceivedOutput::read` accepts forged outputs — funds reported received that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` performs only syntactic validation of attacker-supplied bytes (a canonical scalar, a consensus-decodable `TxOut`, a consensus-decodable `OutPoint`) and returns a `ReceivedOutput` the wallet treats as a confirmed, spendable payment. It never checks that `output.script_pubkey` corresponds to the wallet's key/`offset`, nor that `outpoint` references a transaction whose `vout`-th output equals `output`. This is the Serai analog of the Statamic bug class: a crafted blob passes the nominal "type checks" (`read_F`, `consensus_decode`) while being semantically invalid, exactly like a PHP file crafted to look like an image passes mime-type validation.

### Finding Description
`Scanner::scan_transaction`/`scan_block` are the legitimate producers of `ReceivedOutput`: they look up `output.script_pubkey` in `self.scripts` and construct the triple `(offset, output, OutPoint::new(txid, vout))`, which is internally consistent by construction (`networks/bitcoin/src/wallet/mod.rs:199-214`). `ReceivedOutput::write` serializes exactly this triple (`networks/bitcoin/src/wallet/mod.rs:136-141`).

The inverse path, `ReceivedOutput::read`, does not re-establish that consistency (`networks/bitcoin/src/wallet/mod.rs:121-134`):

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let mut buf_r = BufReader::with_capacity(0, r);
    output = TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
    outpoint = OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    Ok(ReceivedOutput { offset, output, outpoint })
}
```

An unprivileged party controlling these bytes (e.g., a coordinator/peer supplying wallet state, or any pipeline deserializing `ReceivedOutput`s from untrusted storage/transport — the read path is explicitly public and takes arbitrary `Read`) can claim:

1. Any `script_pubkey` — including the wallet's P2TR script, or a script belonging to someone else entirely — with no check against `Scanner::scripts` or the group key.
2. Any `value` — `ReceivedOutput::value()` returns `output.value` verbatim (`networks/bitcoin/src/wallet/mod.rs:116-118`), so an attacker can fabricate an arbitrary received amount.
3. Any `outpoint` — including a nonexistent txid, or a vout pointing at a different output, so the "funds" do not exist on-chain and can never be spent.
4. Any `offset` — even a well-formed output paying the wallet will carry a wrong offset, so the derived spend key (`key + offset·G`) will not match the output key.

None of these inconsistency classes are detectable downstream: the struct fields are private, and the accessors faithfully return the forged values.

### Impact Explanation
A forged `ReceivedOutput` causes the wallet to report funds as received that are not spendable — one of the explicitly accepted impact classes. Concretely:

- **Balance inflation**: an attacker credits the victim an arbitrary `value` at an arbitrary `outpoint`; downstream bookkeeping/UTXO selection accepts it.
- **Unspendable/DoS UTXOs**: inputs built from the forged outpoint will be rejected by the network (nonexistent prevout) or produce an invalid signature (wrong `offset` → wrong signing key), potentially stalling spend construction.
- **Griefing real payments**: even for a real on-chain output paying the wallet, a crafted `offset` makes the actual spend key unrecoverable from the deserialized record.

The check the Scanner performs — membership of `script_pubkey` in `self.scripts` (`networks/bitcoin/src/wallet/mod.rs:205`) — is exactly the semantic validation the read path drops, mirroring how the Statamic advisory's "mime-type validation" was satisfied by a file that was not actually what it claimed.

### Likelihood Explanation
Reachability depends on the integrator deserializing `ReceivedOutput` from an untrusted or corruptible channel; the API is public, `#[cfg(feature = "std")]`, and requires no secret material to forge (a scalar, a `TxOut`, an `OutPoint` — all publicly constructible). No cryptographic break is needed; the "forge" is pure serialization. Severity: Medium — integrity impact on funds tracking with no secret leakage, gated on the untrusted-input assumption.

### Recommendation
Bind the deserialized fields together and to the wallet:

- Have `ReceivedOutput::read` take the expected `ScriptBuf` (or the `Scanner`) and reject outputs whose `script_pubkey` is not a registered script, re-deriving/verifying `offset` against `self.scripts.get(&output.script_pubkey)` rather than trusting the serialized scalar.
- Where possible, verify `outpoint` against the chain (or at minimum document that `ReceivedOutput::read` must only consume trusted, self-produced bytes — the current doc comment only says "Read a ReceivedOutput from a generic satisfying Read").
- Alternatively, treat the serialization as authenticated state (e.g., MAC it) so forged records cannot be injected.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read
let mut buf = Vec::new();
// offset = 0 (or any scalar)
buf.extend(Secp256k1::read_F-compatible encoding of Scalar::ZERO .to_bytes());
// A TxOut with value = 1_000_000_000 sats paying to the wallet's P2TR script
// (or any script — nothing checks it)
let forged_out = TxOut { value: Amount::from_sat(1_000_000_000), script_pubkey: wallet_script };
buf.extend(serialize(&forged_out));
// An outpoint that does not exist on-chain
buf.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));

let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
assert_eq!(ro.value(), 1_000_000_000); // wallet reports 10 BTC received
// ro.outpoint() resolves to nothing on-chain; any spend attempt fails
```

Relevant code: `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:121-134`), missing consistency checks vs. the constructor logic in `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`), and the trusted-field accessors `value`/`outpoint`/`offset` (`networks/bitcoin/src/wallet/mod.rs:100-118`).