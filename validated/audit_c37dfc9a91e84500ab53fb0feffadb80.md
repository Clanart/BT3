### Title
Scanner only recognizes exact-registered P2TR `script_pubkey`s, silently ignoring other spendable output types to the same key — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Biconomy bug was an unintended check that whitelisted only two "types" (`MODULE_TYPE_VALIDATOR`, `MODULE_TYPE_MULTI`) in a path that was supposed to accept all types, causing valid inputs to be rejected. The Serai analog lives in `Scanner`: it maintains a `scripts: HashMap<ScriptBuf, Scalar>` populated exclusively with P2TR scriptPubKeys derived from `key + offset*G`, and `scan_transaction` credits an output only when `output.script_pubkey` matches one of those exact scripts. Any output spendable by the same underlying key but published under a different (still valid) script type — or any key/offset combination for which `p2tr_script_buf` returns `None` — is silently dropped, so received funds are never reported and never enter the spendable UTXO set.

### Finding Description
`Scanner::new` and `Scanner::register_offset` build the recognition set solely via `p2tr_script_buf(self.key + (GENERATOR * offset))`, which itself returns `None` for odd-parity points and only ever emits `ScriptBuf::new_p2tr_tweaked`. `scan_transaction` then performs a single exact `self.scripts.get(&output.script_pubkey)` lookup per output; a mismatch is ignored with no error. This mirrors the reported bug's shape: a "type" check (script template equality) restricts which valid inputs are accepted on a path reachable purely from public data — here, arbitrary Bitcoin transactions broadcast by any unprivileged payer.

An external party who learns the tweaked internal key (it is the public address) can pay the multisig via any script type other than the exact registered P2TR script — e.g., key-path-versus-script-path variants constructed by re-deriving the key under a different tapleaf/commitment, or paying an offset `o` whose even-parity representative was already taken by an earlier-registered offset (the `register_offset` increment-until-even rule makes offsets surjective, not bijective, so two different logical offsets can map to the same script and the second registration returns `None`). In each case the Bitcoin is spendable by the threshold key in principle, yet `scan_transaction`/`scan_block` never surfaces it as a `ReceivedOutput`, so it is never scheduled for spending.

### Impact Explanation
Funds actually received on-chain are not reported as received and never become spendable through the wallet's normal `ReceivedOutput` flow — the "funds reported received that are not spendable" (here: never reported at all) impact class. Because detection is purely a `script_pubkey` whitelist, no error is raised; the outputs are simply invisible to the scanner and effectively burned from the protocol's accounting perspective.

### Likelihood Explanation
Any unprivileged party can send Bitcoin transactions (explicitly an allowed public input). Triggering the miss only requires constructing an output whose `script_pubkey` doesn't byte-match a registered script while still being bound to the scanned key material — for example by exploiting the surjective offset mapping (`offset` and `offset + 1` colliding to the same even-parity script) so a later registration silently fails with `None`, after which payments to that offset's intended script are never detected.

### Recommendation
As in the reported fix, remove the unintended restriction: instead of whitelisting only `p2tr_tweaked` scriptPubKeys, the scanner should recognize every output type spendable by the key (or at minimum, document and enforce at the API level that only exact registered P2TR scripts are valid deposit targets, and make `register_offset` collisions a hard error rather than a silent `None`). Audit `p2tr_script_buf`'s `None` path and the `contains_key → None` collision path in `register_offset` so that unusable keys/offsets cannot cause deposits to be silently ignored.

### Proof of Concept
```rust
// Scanner for a tweaked threshold key
let mut scanner = Scanner::new(group_key).unwrap();

// Register two offsets where the second is odd and increments into
// the first offset's already-registered even script.
let a = scanner.register_offset(o_even).unwrap();   // Some(o_even)
let b = scanner.register_offset(o_colliding);        // returns None silently

// Attacker/payer funds a tx paying to the script that `o_colliding`
// logically refers to (or any non-P2TR script bound to the same key).
// scan_transaction finds no scripts.get(&output.script_pubkey) match
// and returns an empty Vec<ReceivedOutput> — the deposit is invisible.
assert!(scanner.scan_transaction(&attacker_tx).is_empty());
```

Note: within my available search depth I verified the whitelist structure (`scripts` map, `p2tr_script_buf` P2TR-only emission, `register_offset` collision → `None`, exact-match `scan_transaction`) in `networks/bitcoin/src/wallet/mod.rs`, but did not exhaustively confirm whether a higher layer performs additional scanning that would mitigate the miss; if such a pass exists, the finding reduces to the `register_offset` silent-collision case.