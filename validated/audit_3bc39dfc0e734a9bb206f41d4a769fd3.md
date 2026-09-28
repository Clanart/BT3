### Title
`ReceivedOutput::read` skips the offset/script_pubkey consistency check enforced by `Scanner` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class is a validation check applied on one construction path but omitted on a parallel path (`valid_folder_path?` enforced by the local uploader but not the AWS uploader). The Serai analog is `ReceivedOutput`: the `Scanner` path (`scan_transaction`) only ever produces a `ReceivedOutput` whose `script_pubkey` is provably the P2TR output for `key + offset*G`, because the offset was itself recovered by looking the script up in `self.scripts`. The deserialization path, `ReceivedOutput::read`, accepts the same three fields (`offset`, `output`, `outpoint`) from raw bytes with no such consistency check, letting a caller instantiate a `ReceivedOutput` claiming an arbitrary offset for an output paying to an arbitrary, unrelated script.

### Finding Description
`Scanner::register_offset` inserts `p2tr_script_buf(self.key + GENERATOR*offset) -> offset` into `self.scripts`, and `scan_transaction` only emits a `ReceivedOutput` when `output.script_pubkey` is found in that map, so the invariant `output.script_pubkey == p2tr(key + offset*G)` holds by construction (networks/bitcoin/src/wallet/mod.rs:180-214).

`ReceivedOutput::read` reconstructs the struct by reading an unchecked `Scalar` offset and then consensus-decoding an arbitrary `TxOut` and `OutPoint`, with no verification that `TxOut.script_pubkey` corresponds to `key + offset*G` (networks/bitcoin/src/wallet/mod.rs:122-134). The same split exists downstream: `Output::key()` derives the key as `script_key - offset*G` and merely `assert!`s the script is P2TR, so a crafted `ReceivedOutput` makes `key()` return an attacker-chosen point rather than the multisig key (processor/src/networks/bitcoin.rs:112-122). Additionally `Output::read` unwraps a fallible SCALE decode and `Address::try_from` (processor/src/networks/bitcoin.rs:148-153), but that is only a panic.

The net effect: bytes written into a `ReceivedOutput` via `read`/`serialize` round-trip are trusted to satisfy an invariant that is never re-verified, exactly mirroring the AWS uploader trusting `file` without `valid_folder_path?`.

### Impact Explanation
A `ReceivedOutput` claiming `offset = 0` (or a registered offset such as the `branch`/`change`/`forward` offsets derived in `processor/src/networks/bitcoin.rs:333-344`) but carrying a `TxOut` paying to an attacker-controlled P2TR key will be treated as spendable by the wallet. `SignableTransaction`/`TransactionMachine` will generate a key-path signature for `key + offset*G`, which does not satisfy the output's actual `script_pubkey`, producing an invalid transaction — i.e., funds reported as received that are not spendable — and potentially stalling a batch of otherwise-valid inputs. Via `Output::key()`, the same primitive lets an attacker cause the processor to attribute an output to a key that was never generated for it.

### Likelihood Explanation
Reachability requires untrusted bytes to reach `ReceivedOutput::read` (e.g., outputs synchronized or restored from an untrusted store rather than produced by `Scanner::scan_transaction`). The rules explicitly list `ReceivedOutput::read` as an in-scope untrusted-bytes sink, so the path is reachable by construction of the audit model. Exploitation requires no key material — only a crafted `(offset, TxOut, OutPoint)` triple — making this a Medium-severity integrity violation rather than a key-recovery issue.

### Recommendation
Either make the invariant unforgeable by construction, or re-verify it on deserialization: have `ReceivedOutput::read` (or a `verify(key)` method called before use) check `output.script_pubkey == p2tr_script_buf(key + GENERATOR*offset)` for the key the output is claimed under. Downstream, `Output::key()` should `expect`/error rather than silently derive a key from an unverified script, and `Output::read` should return `io::Error` instead of `unwrap()`ing the SCALE decode of `presumed_origin`.

### Proof of Concept
```rust
// Construct a ReceivedOutput paying to an attacker key while claiming offset 0.
let attacker_key = ProjectivePoint::GENERATOR * Scalar::from(42u64);
let evil_script = p2tr_script_buf(attacker_key).unwrap();

let forged = ReceivedOutput {
  offset: Scalar::ZERO,                                    // claims: belongs to `key`
  output: TxOut { value: Amount::from_sat(100_000), script_pubkey: evil_script },
  outpoint: OutPoint::new(Txid::all_zeros(), 0),
};

let bytes = forged.serialize();
let parsed = ReceivedOutput::read::<&[u8]>(&mut bytes.as_ref()).unwrap(); // succeeds

// parsed.value() == 100_000 and parsed.offset() == 0, so downstream code treats it as
// spendable by `key`, yet its script_pubkey pays to attacker_key.
// SignableTransaction::new(vec![parsed], ...).multisig(&keys) then emits a signature for
// `key` that cannot satisfy the output -> invalid transaction / unspendable "received" funds.
// Output::key() over `parsed` returns `attacker_key - 0*G` = attacker_key, misattributing it.
```