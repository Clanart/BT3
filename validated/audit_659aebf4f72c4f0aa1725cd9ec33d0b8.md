### Title
Deserialized `ReceivedOutput`s bypass the script-pubkey↔offset binding enforced by `Scanner`, letting untrusted bytes attribute arbitrary outputs to arbitrary keys - (networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` enforces that an output is only accepted as "received" if its `script_pubkey` is the exact P2TR script derived from `key + offset·G` for a registered offset (a lookup into `self.scripts`, `scan_transaction`). However, `ReceivedOutput::read` — the deserialization path for the same type — reads an `offset` scalar, a `TxOut`, and an `OutPoint` from untrusted bytes with no consistency check whatsoever. The two construction paths therefore admit disjoint sets of values: the byte path accepts `(offset, script_pubkey)` pairs the scanner path would always reject.

### Finding Description
`ReceivedOutput` couples an output to the tweaked key that can spend it: `offset` such that the spend key is `group_key + offset·G`. The only place the `offset ↔ script_pubkey` relation is enforced is inside `Scanner`:

- `Scanner::register_offset` inserts `p2tr_script_buf(key + offset·G)` into `self.scripts` (networks/bitcoin/src/wallet/mod.rs:180-196).
- `Scanner::scan_transaction` only yields a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns a registered offset (networks/bitcoin/src/wallet/mod.rs:199-214).

`ReceivedOutput::read`, by contrast, decodes `offset` via `Secp256k1::read_F` and `output`/`outpoint` via `consensus_decode` and returns the pair unconditionally (networks/bitcoin/src/wallet/mod.rs:122-134). Downstream, `Output::key()` reconstructs the attributed key as `script_pubkey_key − offset·G` (processor/src/networks/bitcoin.rs:112-122) and `Output::read` wraps `ReceivedOutput::read` again with no binding check (processor/src/networks/bitcoin.rs:145-166).

An attacker who controls `ReceivedOutput`/`Output` bytes can therefore:
1. Report an output under *any* key of their choosing: pick any victim script `S` carrying key `K_S` and any offset `o`; `key()` returns `K_S − o·G`, which need not be a key Serai controls.
2. Attribute an output to a *specific registered Serai key* `K` while making it unspendable by that key: pick `S` paying to `K + o·G` for an `o` that was never registered — the scanner path would never accept this pairing, but the read path does.

### Impact Explanation
Funds reported received that are not spendable. Outputs accepted through `read` can be credited to a multisig key whose spending path (which tweaks the threshold key by `output.offset()` when producing the Schnorr signature via `ThresholdKeys::offset` / `SignableTransaction::multisig`) does not actually correspond to the output's `script_pubkey`, or can be credited to a key that isn't Serai's at all. Either way the coordinator/accounting records a balance that cannot be moved on-chain — the analog of traffic being admitted that the enforcement path (`Scanner`) was designed to reject.

### Likelihood Explanation
`ReceivedOutput::read` and `Output::read` are explicitly reachable deserialization APIs for untrusted bytes (they're used to persist/reload scanned outputs across DB and peer/machine boundaries). The missing check is structural — a single `read_G`-derived comparison — so any caller feeding attacker-influenced bytes into these `read` functions triggers the bypass deterministically. Severity is Medium: it does not forge signatures or leak keys, but it falsifies the ledger of spendable funds.

### Recommendation
Bind the offset to the script at deserialization: in `ReceivedOutput::read` (and/or `Output::read`), recompute `p2tr_script_buf(derived_key)` semantics — i.e., verify the decoded `script_pubkey` is a P2TR output whose internal key equals `attributed_key + offset·G` for the attributed key — or restrict construction of `ReceivedOutput` to `Scanner` so only scan-validated instances can exist. At minimum, re-validate `key() + offset·G` against `script_pubkey` when `Output::key()` is computed and reject inconsistent values instead of silently deriving a synthetic key.

### Proof of Concept
```rust
// Attacker-chosen bytes: script_pubkey pays to arbitrary K_S, offset arbitrary.
let k_s = ProjectivePoint::random(&mut OsRng);
let script = p2tr_script_buf(k_s + ProjectivePoint::GENERATOR).unwrap(); // any valid p2tr
let offset = Scalar::random(&mut OsRng);                                  // never registered

let mut bytes = offset.to_bytes().to_vec();
bytes.extend(serialize(&TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: script.clone(), // NOT p2tr of (serai_key + offset*G)
}));
bytes.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));

let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted unconditionally
// ro.offset() == offset, ro.output().script_pubkey == script
// Output::key() derives script_key - offset*G, attributing the output to a key
// that Scanner::scan_transaction would never report for these bytes.
```