### Title
`ReceivedOutput::read` accepts a serialized `offset` without verifying it derives the output's `script_pubkey`, allowing arbitrary unspendable outputs to be registered as received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

The audited bug is a missing legitimacy check: `UniV2Controller.removeLiquidity` decodes `(tokenA, tokenB)` from untrusted calldata and returns them as `tokensIn`, which `AccountManager.exec` then registers as account assets without any allowlist/membership validation — any decoded value becomes a credited asset.

The analog in Serai is `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122`). `ReceivedOutput` is the "asset record" type of the Bitcoin wallet: it pairs an on-chain `TxOut`/`OutPoint` with a scalar `offset` claiming "key + offset·G produces this output's script_pubkey". `scan_transaction` only ever creates consistent pairs (it inserts into `scripts` map keyed by `p2tr_script_buf(key + offset·G)`, `wallet/mod.rs:180-196, 205`), but `read` deserializes the three fields independently and never re-checks that `offset` actually maps to `output.script_pubkey`. Untrusted bytes fed to `ReceivedOutput::read` therefore produce a `ReceivedOutput` claiming ownership of an arbitrary `TxOut` under an arbitrary offset — the exact "illegitimate tokensIn added as asset" shape.

### Finding Description

`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`):

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;                       // scalar, canonicality checked only
    output  = TxOut::consensus_decode(...)?;                  // any TxOut accepted
    outpoint = OutPoint::consensus_decode(...)?;              // any outpoint accepted
    Ok(ReceivedOutput { offset, output, outpoint })           // no consistency check
}
```

The only validation is scalar canonicality via `read_F`. There is no check that `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey`, nor that `outpoint` refers to a real output containing that `TxOut`, nor that the offset is one the owning `Scanner` actually registered.

The consumable consequence is concrete: `Output::key()` (`processor/src/networks/bitcoin.rs:112-122`) recovers the owning group key by computing `script_pubkey's x-only key − offset·G`. With a mismatched `offset`, this yields a group key that does not correspond to the output — an output credited to (and reported as a balance of) key A while actually being locked to unrelated key B. Such an output can be fed into `SignableTransaction::new`/`multisig` as an input; signing then either targets the wrong key (FROST shares verify against the real verification shares and `complete` fails) or produces a transaction spending an outpoint whose script is not controlled by the derived key — funds reported received that are not spendable.

This mirrors the report's root cause precisely: a decode routine trusts attacker-supplied identifiers, and a downstream accounting step (`_updateTokensIn` / output-crediting via `key()` and `scan`-equivalent bookkeeping) treats them as legitimate assets.

### Impact Explanation

An unprivileged party who can supply bytes to `ReceivedOutput::read` (the serialization channel between the scanner/coordinator and downstream processors — exactly the `Read` path enumerated as attacker-controlled input) can cause:

- A `ReceivedOutput` to be recorded pairing an arbitrary on-chain `TxOut` with an unrelated offset → `Output::key()` credits it to the wrong group key.
- Funds "received" that are not spendable: the wallet/DKG key cannot produce a valid signature for the claimed output (the tweaked key `key + offset·G` does not match the script's committed key, and TapTweak signing in `SignableTransaction` would sign for the wrong key or panic on odd-Y intermediate keys).
- Balance misattribution across distinct multisigs sharing a processor, since `key()` is the routing signal for which threshold group owns an output.

### Likelihood Explanation

Reachable whenever serialized `ReceivedOutput`/`Output` blobs cross a trust boundary (relay of scanned outputs, DB restoration, coordinator→signer handoff) — `read` is the documented public deserialization API for exactly this wire format. The crafted input requires only a canonical scalar plus valid consensus encodings — trivially constructible. Severity Medium: it does not leak key material or forge signatures, but it deterministically produces non-spendable credited funds / misattributed balances, matching the medium-severity "arbitrary asset registration" of the source report.

### Recommendation

Bind the offset to the output at deserialization time, or make the trust boundary explicit:

- Add a `ReceivedOutput::read_for(scanner_key, r)` (or `verify(&self, key)`) that recomputes `p2tr_script_buf(key + GENERATOR * offset)` and rejects the object unless it equals `output.script_pubkey`, mirroring the check `Scanner::register_offset`/`scan_transaction` already performs at creation time (`wallet/mod.rs:185-190`).
- Alternatively, store only `outpoint` + `offset` and re-derive `output` from chain data, so a fabricated `TxOut` cannot be injected.
- In `processor/src/networks/bitcoin.rs`, `Output::read` should additionally verify `outpoint` resolves on-chain before the output is credited, and the `.unwrap()`s on `Option::<Vec<u8>>::decode`/`Address::try_from` (lines 150-152) should return `io::Error` rather than panic on malformed input.

### Proof of Concept

```rust
// Attacker crafts bytes: offset = 1, but TxOut pays to an unrelated P2TR key.
let real_key = ProjectivePoint::random(&mut OsRng);         // some honest multisig key
let victim   = ProjectivePoint::random(&mut OsRng);         // attacker's unrelated key

let forged = {
    let mut buf = vec![];
    // offset that does NOT correspond to the script below
    buf.extend(Scalar::ONE.to_bytes());
    buf.extend(serialize(&TxOut {
        value: Amount::from_sat(50_000),
        script_pubkey: p2tr_script_buf(victim).unwrap(),    // script for victim, not real_key + G
    }));
    buf.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));
    buf
};

let ro = ReceivedOutput::read(&mut forged.as_slice()).unwrap(); // accepted — no consistency check

// Downstream: key() derives script_key - offset*G = victim_key - G, i.e. a key that
// does not control the output and is not `real_key`. The output is credited to a
// wrong/unknown group key; any SignableTransaction spending it cannot be completed
// by the real multisig. Funds are reported received yet are not spendable.
```

Contrast with the legitimate construction path at `wallet/mod.rs:205-211`, where `offset` is only ever attached to outputs whose `script_pubkey` was looked up in `self.scripts` — an invariant `read` fails to re-establish.