### Title
`ReceivedOutput::read` trusts the serialized scalar offset instead of re-deriving it from the output's `script_pubkey`, allowing forged "received" outputs that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Traefik bug class is: a security-relevant check is evaluated against an attacker-chosen field (`backendRef.namespace`) rather than the authoritative origin (`route.Namespace`), and the discrepancy exists only in one of two code paths. `bitcoin-serai` exhibits the same shape: `Scanner::scan_transaction` derives the spend offset authoritatively — it looks up `self.scripts[&output.script_pubkey]`, so the returned `ReceivedOutput.offset` is provably the scalar that turns the scanner key into the key committed by the script. But `ReceivedOutput::read` — the deserialization path for the same object — accepts the `offset` field verbatim with no check that `p2tr_script_buf(scanner_key + offset*G)` equals `output.script_pubkey`. Any consumer of a deserialized `ReceivedOutput` that relies on `offset()` to spend or account for the output is trusting a claimed value rather than the validated one, exactly as Traefik trusted `backendRef.namespace` in the WRR path.

### Finding Description
- `Scanner::scan_transaction` (wallet/mod.rs) builds each `ReceivedOutput` by matching `output.script_pubkey` against `self.scripts`, a `HashMap<ScriptBuf, Scalar>` populated only by `Scanner::new`/`register_offset`. The offset stored in the result is therefore bound to the on-chain script.
- `ReceivedOutput::read` (wallet/mod.rs ~lines 122-134) reads `offset` via `Secp256k1::read_F`, then consensus-decodes `output` and `outpoint`, and returns them unconditionally. There is no `Scanner` parameter and no script-vs-offset consistency check — nothing ties `offset` to `output.script_pubkey`.
- The type's own documentation defines `offset` as "the scalar offset to obtain the key usable to spend this output" (line 91). `register_offset` further documents that the registered script is `key + offset*G` encoded as a bare x-only P2TR output (no internal-key/script-path separation — `TweakedPublicKey::dangerous_assume_tweaked` at line 85). Spending therefore depends entirely on `offset()` being correct for the script.
- Untrusted bytes reaching `ReceivedOutput::read` (e.g., serialized outputs exchanged between components, or `Output::read` wrappers that embed a `ReceivedOutput` — the processor's `Output::read` calls `ReceivedOutput::read` at processor/src/networks/bitcoin.rs:156, though that crate is out of scope) can declare an arbitrary offset for a real-looking output.

### Impact Explanation
Two concrete consequences for an unprivileged party able to feed bytes to `ReceivedOutput::read`:

1. **Funds reported received that are not spendable**: an attacker supplies `(offset', output, outpoint)` where `output.script_pubkey` corresponds to `key + offset*G` but `offset' != offset`. The object reports itself as a spendable input; when it is later passed to `SignableTransaction`/`multisig`, the threshold signs under the wrong key (`key + offset'*G`), producing a per-input BIP-340/Taproot signature that fails on-chain — the input cannot be spent, and any fee/change math built around it is wasted or stuck.
2. **Misattribution**: the offset also selects which logical key/branch an output belongs to (the codebase distinguishes External/Branch/Change/Forwarded by offset elsewhere). A forged offset relabels the output's purpose, breaking accounting and change handling.

Both flows require no key compromise and no cooperation from other participants — just attacker-controlled bytes on a `read` path that the library defines as public.

### Likelihood Explanation
Reachability is explicitly granted: `ReceivedOutput::read` is one of the listed untrusted-byte entry points. The exploitable precondition is that some consumer deserializes `ReceivedOutput`s from a source the attacker influences rather than obtaining them solely from `scan_transaction`. Given `write`/`serialize` exist precisely to move these objects between components (and at least the out-of-scope processor serializes/deserializes outputs containing them through `Output::read`), this is a realistic path rather than a hypothetical one. The defect itself is unconditional — `read` performs zero validation — so reliability is deterministic whenever reached. Impact is bounded to mis-accounted/unspendable inputs (Medium) rather than key recovery, since the attacker cannot choose an offset that makes a foreign script spendable without solving discrete log.

### Recommendation
- Give `ReceivedOutput` a validating constructor/read that takes the scanner's base key (or the `Scanner`) and verifies `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)`, returning an error on mismatch — mirroring how `scan_transaction` establishes the binding.
- Alternatively, make `read` crate-private and expose only `Scanner::scan_transaction`/`scan_block` as the source of `ReceivedOutput`s, plus a `ReceivedOutput::verify(&self, key)` API for deserializers.
- Note that `Output::read` in processor code would need to thread the key through for this check.

### Proof of Concept
```rust
use bitcoin::{Transaction, TxOut, Amount, ScriptBuf, OutPoint, Txid, hashes::Hash};
use secp256kfun::profun::{Scalar, ProjectivePoint};
use bitcoin_serai::wallet::{Scanner, ReceivedOutput};

// A real output paying to the scanner key with offset o
let key = even_key();
let mut scanner = Scanner::new(key).unwrap();
let real_offset = scanner.register_offset(Scalar::random(&mut rng)).unwrap();
let script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * real_offset).unwrap();
let txout = TxOut { value: Amount::from_sat(100_000), script_pubkey: script };

// Attacker swaps in a different registered offset (e.g., the ZERO external offset)
let forged = ReceivedOutput {
    // serialized form: offset || TxOut || OutPoint — offset field attacker-controlled
    offset: Scalar::ZERO,           // claims the *base* key controls this output
    output: txout,
    outpoint: OutPoint::new(txid, 0),
};
let bytes = forged.serialize();
let decoded = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted
assert_eq!(decoded.offset(), Scalar::ZERO);
// decoded.output().script_pubkey is actually key + real_offset*G.
// Any spend built with decoded.offset() produces a signature under key + 0*G,
// which does not match the script's committed key -> input unspendable,
// while being reported as a normally received output.
```
`ReceivedOutput::read` performs no check linking `offset` to `output.script_pubkey`; `scan_transaction` is the only path that establishes that binding.