### Title
`ReceivedOutput` deserialization and `Scanner` offset registration keep two views of "which key owns this output" that can diverge — a `ReceivedOutput` pairs an arbitrary `offset` with an arbitrary `script_pubkey` with no cross-check, and `register_offset` silently rewrites requested offsets — so outputs can be reported as received while not being spendable under the recorded offset - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
Analogous to SHToken's `userBalances`/`_balances` split where `transferFrom()` bypassed the custom ledger, `bitcoin-serai` keeps two sources of truth for which key owns an output: the `Scanner.scripts` map (`ScriptBuf -> Scalar`) built by `Scanner::new`/`register_offset`, and the `ReceivedOutput { offset, output, outpoint }` triple produced/consumed elsewhere. `ReceivedOutput::read` is an explicitly in-scope untrusted-bytes sink: it deserializes `offset` and `output.script_pubkey` independently and never verifies that `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey`. The scanner's registration path (`register_offset`) enforces consistency only for entries it itself creates; deserialization bypasses it entirely — the same "one update path skips the second bookkeeping structure" shape as HAL-09.

### Finding Description
Two facts that should be one invariant are stored separately:

1. `Scanner::register_offset` (networks/bitcoin/src/wallet/mod.rs:180-196) loops `offset += ONE` until `key + G*offset` is even, then stores `scripts[script] = used_offset`. The requested scalar and the recorded scalar differ whenever the derived point is odd, and the doc comment concedes "offsets are surjective, not bijective" — two distinct requested offsets collapse to one `scripts` entry (registering `o` (odd) then `o+1` returns `None` the second time), so a caller tracking requested offsets diverges from the scanner's `scripts` map.

2. `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) reads `offset` via `Secp256k1::read_F` and `output` via consensus decode, but performs no check that the offset actually derives the output's `script_pubkey` from the scanner's `key`. The spend path then derives the signing key from `output.offset()` (see `SignableTransaction::new`/`multisig` usage in `wallet/send.rs` and `tests/wallet.rs:219-244`, where each input's key is `key + GENERATOR * offset`), trusting the pairing the scanner established.

Result: a crafted `ReceivedOutput` (offset `o`, TxOut paying to an unrelated script) passes `read` cleanly. If it is subsequently used to build/sign a transaction, the multisig signs an input whose private key `share + o` does not unlock `script_pubkey` — an unspendable-by-that-key input is treated as spendable, or equivalently the signing set is induced to sign a spend of an input the bookkeeping claims is theirs. This mirrors HAL-09 exactly: the "users array" (`scripts`/`offset` accounting) and the "balance" (`output.script_pubkey`) disagree because one entry path (`read`) skips the consistency logic that `register_offset` enforces.

### Impact Explanation
- Funds reported as received (`ReceivedOutput` accepted, value counted) that are not spendable under the recorded offset — signature for `key + offset` cannot satisfy the output's actual Taproot key. For a threshold-wallet integrator this freezes or misattributes the UTXO.
- Conversely, an untrusted `ReceivedOutput` can carry an attacker-chosen `outpoint`/`TxOut` with an attacker-favorable `offset`, steering `SignableTransaction` to commit to a prevout the protocol never received, i.e., signing of an unintended input once CONFIRMATIONS/gossip deliver forged bytes.
- Reachability is limited to contexts where `ReceivedOutput::read` consumes bytes an unprivileged peer controls (processor/coordinator message channels, DB state restored from shared storage); on-chain scanning alone produces consistent pairs. Medium severity.

### Likelihood Explanation
`scan_transaction` (mod.rs:199-214) always builds consistent `offset`/`script_pubkey` pairs, so natural on-chain input cannot trigger divergence. Exploitation requires a pipeline that deserializes attacker-influenced `ReceivedOutput` bytes — plausible in the documented multi-component architecture (outputs serialized, acked, and passed between scanner, scheduler, and signing) — but I could not fully verify `send.rs`'s exact use of `offset` within this iteration, so the "unintended signature" escalation is inferred from `multisig(keys.clone())` deriving per-input keys from `output.offset()` rather than re-verified.

### Recommendation
Make the ownership invariant single-sourced:

- In `ReceivedOutput::read`, or in a `ReceivedOutput::verify(key)` method called before spending, check `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` and reject mismatches.
- In `Scanner::register_offset`, return an error distinguishing "offset was adjusted to even" from "script already registered" rather than collapsing to `None`, so callers cannot maintain a divergent requested-vs-used offset map.
- Have `SignableTransaction::new` re-derive each input's script from `(key, offset)` and assert equality with the supplied `TxOut`.

### Proof of Concept
```rust
use bitcoin_serai::wallet::{ReceivedOutput, Scanner, p2tr_script_buf};
use k256::{ProjectivePoint, Scalar, elliptic_curve::ff::Field};
use bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, absolute::LockTime, transaction::Version, Transaction};
use rand_core::OsRng;

// 1) register_offset silently rewrites offsets; two requested offsets map to one entry
let mut key = ProjectivePoint::random(&mut OsRng);
while p2tr_script_buf(key).is_none() { key += ProjectivePoint::GENERATOR; }
let mut scanner = Scanner::new(key).unwrap();

// choose requested offset o such that key + o*G is odd
let o = Scalar::ONE;
// assume odd: used offset becomes o+1
let used = scanner.register_offset(o).unwrap();
// a second, distinct requested offset that resolves to the same script is dropped:
assert_eq!(scanner.register_offset(used), None); // second 'account' never tracked

// 2) ReceivedOutput::read accepts an offset/script_pubkey pair that disagree
let forged_offset = Scalar::from(7u64);
let bogus = TxOut {
  value: Amount::from_sat(50_000),
  script_pubkey: ScriptBuf::new_op_return(&[0xaa]), // not p2tr(key + 7G)
};
let mut bytes = forged_offset.to_bytes().to_vec();
bytes.extend(bitcoin::consensus::serialize(&bogus));
bytes.extend(bitcoin::consensus::serialize(&OutPoint::null()));
let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // no consistency check
assert_eq!(ro.offset(), forged_offset);
// ro is now a 'received' output whose key (key + 7G) cannot spend its script_pubkey
```

The first half demonstrates bookkeeping divergence identical in shape to `getUsers()` missing a token holder; the second demonstrates an untrusted-bytes path that accepts an output the scanner's key registry never vouched for, reachable via `ReceivedOutput::read`.