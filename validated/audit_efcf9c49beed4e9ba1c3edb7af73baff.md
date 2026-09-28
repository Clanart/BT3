### Title
`ReceivedOutput::read` accepts arbitrary `(offset, TxOut)` pairs without verifying the output pays to `key + offset·G`, registering non-wallet outputs as spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes an offset scalar, a `TxOut`, and an `OutPoint` from untrusted bytes, but never checks that `output.script_pubkey` is the P2TR script for `key + offset·G`. The downstream `Output::key()` in the processor then *derives* the credited key by subtracting `offset·G` from whatever key the attacker-chosen script commits to. This is the direct analog of the OpenQ bug: a deposit path accepts a wrong-typed/mismatched asset and registers it as a normal (spendable) deposit, when it is not.

### Finding Description
In the original report, `fundBountyToken()` registered an ERC721 transfer as an ERC20 deposit because `receiveFunds()` didn't validate the token type, making the deposit unrefundable.

In `networks/bitcoin/src/wallet/mod.rs:122-134`, `ReceivedOutput::read` performs three unchecked steps:
- `Secp256k1::read_F(r)` — any scalar accepted as `offset`
- `TxOut::consensus_decode` — any `script_pubkey`/`value` accepted
- `OutPoint::consensus_decode` — any txid/vout accepted

No relation between the three fields is enforced. Contrast with `Scanner::scan_transaction` (`wallet/mod.rs:199-214`), the legitimate producer, which only emits a `ReceivedOutput` when `output.script_pubkey` is present in `self.scripts` — i.e., provably `new_p2tr_tweaked(key + offset·G)` for a registered offset. `read` drops this invariant entirely.

The misclassification is realized in `processor/src/networks/bitcoin.rs:112-122`, `Output::key()`:

```rust
Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap()
  - (ProjectivePoint::GENERATOR * self.output.offset())
```

It takes the x-only key *out of the attacker-supplied script* and subtracts `offset·G`, then reports the result as the owning multisig key. For a forged `ReceivedOutput` whose `TxOut` pays to an attacker's own P2TR key `A`, the output is credited to group key `A − offset·G` — a key the validator set does not control and cannot sign for. If the script isn't P2TR at all, `assert!(script.is_p2tr())` panics outright.

### Impact Explanation
Any consumer that ingests `ReceivedOutput` bytes (the `read` path is exposed for exactly this) will treat an attacker-crafted output as a spendable wallet deposit:
- Funds are reported received under a group key that is not actually spendable — mirroring "deposit registered as non-NFT → unrefundable." Accounting/rotation logic that trusts `Output::key()`/`balance()` miscredits deposits and may attempt signing sessions for an unspendable input, or halt on the `is_p2tr` panic.
- The attacker bears only the cost of crafting bytes (and optionally funding the referenced outpoint), so this is a cheap integrity violation rather than theft of attacker funds.

### Likelihood Explanation
Reachable by any unprivileged party that can cause `ReceivedOutput::read` to process chosen bytes (listed as an untrusted-byte sink). No key compromise, collusion, or malformed-curve inputs needed — the encoding is fully canonical; the missing check is purely semantic. Rated Medium: concrete misaccounting/unspendable-output acceptance with no direct theft path.

### Recommendation
Make `ReceivedOutput` self-validating: either store the base `key` in the serialization and verify `output.script_pubkey == p2tr_script_buf(key + generator·offset)` in `read`, or change the API so `ReceivedOutput` can only be constructed via `Scanner` (make fields private and `read` take a `&Scanner`/base key to re-derive and confirm the script). Reject non-P2TR or non-matching scripts with an `io::Error` instead of accepting them.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read
let attacker_scalar = Scalar::random(&mut OsRng);
let attacker_key = ProjectivePoint::GENERATOR * attacker_scalar; // even-Y adjusted
let fake = {
    let mut buf = vec![];
    // offset: any scalar, e.g. ZERO
    buf.extend(Scalar::ZERO.to_bytes());
    // TxOut paying to the *attacker's* P2TR script, not key + offset·G
    buf.extend(serialize(&TxOut {
        value: Amount::from_sat(100_000),
        script_pubkey: p2tr_script_buf(attacker_key).unwrap(),
    }));
    // Any outpoint
    buf.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));
    buf
};
let ro = ReceivedOutput::read(&mut fake.as_slice()).unwrap(); // accepted

// processor Output::key() then reports owner = attacker_key - 0·G = attacker_key,
// i.e. the output is credited to a key the validator set does not control,
// yet is treated as a spendable received output.
```

Root cause confirmed at `networks/bitcoin/src/wallet/mod.rs:122-134` (no offset↔script binding) and the misattribution at `processor/src/networks/bitcoin.rs:112-122` (`key()` derives ownership from the untrusted script rather than verifying it).