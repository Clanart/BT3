### Title
A single unspendable/mismatched input poisons the entire `SignableTransaction`, freezing all pooled funds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::multisig` validates every input's offset-derived key against its prevout `script_pubkey` inside one loop. If any single input fails the check — including when `p2tr_script_buf` returns `None` because the offset-derived group key has odd Y — the entire function returns `None`, making it impossible to spend *any* of the other, perfectly valid inputs. This mirrors the report's class: a global pooling operation where one "blocked" element (one asset whose transfer reverts → one input whose key derivation fails) prevents liquidation/spending of all elements.

### Finding Description
In `SignableTransaction::multisig` (networks/bitcoin/src/wallet/send.rs:273-285):

```rust
pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }
      ...
    }
```

Two failure modes both collapse the whole batch:

1. `p2tr_script_buf` returns `None` for an odd-Y point (networks/bitcoin/src/wallet/mod.rs:80-86). `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) accepts an arbitrary `Scalar` offset via `Secp256k1::read_F` from untrusted bytes. An offset chosen so `keys.offset(offset).group_key()` is odd makes `p2tr_script_buf` return `None`, and the `?` operator aborts `multisig` for every input.

2. Even for an even key, a mismatch between the derived script and `prevouts[i].script_pubkey` hits `None?` and aborts the entire batch — even though the transaction could have been constructed from the remaining valid inputs.

Inputs are pooled globally: `SignableTransaction::new` takes `Vec<ReceivedOutput>` and produces one atomic transaction; `multisig` creates one `AlgorithmMachine` per input (send.rs:281); `TransactionSignMachine::sign` and `TransactionSignatureMachine::complete` likewise abort the entire transaction on any single input's failure (send.rs:355-397, 413-428), with no partial-spend or input-exclusion path.

### Impact Explanation
An unprivileged party able to feed `ReceivedOutput`s into a wallet/coordinator flow (the bytes reach `ReceivedOutput::read`, an explicitly in-scope untrusted-bytes path) can include one poisoned output — an offset producing an odd group key, or a mismatched `script_pubkey` — so that any `SignableTransaction` built over the output set returns `None` from `multisig`. Every legitimately received, spendable output bundled in that transaction becomes unspendable through this API. This is the Serai analog of "one blocked collateral asset prevents liquidation of all assets and creates bad debt": one bad input locks up all pooled funds. Funds are reported received (they appear in `scan_transaction`/`ReceivedOutput`s and are summed into `input_sat`) yet cannot be spent.

### Likelihood Explanation
The attack requires the ability to inject or influence the `ReceivedOutput` set consumed by `SignableTransaction::new`/`multisig`. `register_offset` only stores even-key scripts (mod.rs:180-196), so outputs derived purely from the local `Scanner` are safe; the reachable path is untrusted `ReceivedOutput` bytes via `ReceivedOutput::read` (e.g., a coordinator distributing scan results, or deserialized wallet state). That matches the medium severity of the source report: conditional reachability, but a deterministic full-batch DoS once reached, with no blame or isolation for the bad input.

### Recommendation
Isolate per-input failure instead of pooling: `multisig` should skip (or return a per-input error identifying) the input whose offset-derived key is odd or mismatched, and build the transaction over the remaining inputs — mirroring the report's recommendation to operate per-asset rather than globally. At minimum, `SignableTransaction` construction should validate each `ReceivedOutput`'s `script_pubkey` against `p2tr_script_buf(keys.offset(offset).group_key())` up front and reject/sanitize the bad element, so a single poisoned output cannot freeze the whole spend. Note this still wouldn't fix the case where the offset itself is attacker-chosen to be non-spendable; inputs that don't correspond to locally registered offsets should be rejected.

### Proof of Concept
1. A signer holds `ThresholdKeys<Secp256k1>` `K` and legitimately receives output `O1` via `Scanner` (valid offset `d1`, matching script).
2. An attacker supplies serialized `ReceivedOutput` `O2` via `ReceivedOutput::read` with an offset `d2` chosen such that `K.offset(d2).group_key()` has odd Y (≈50% of arbitrary offsets qualify; simply increment until odd), plus any valid `TxOut`/`OutPoint` bytes.
3. `SignableTransaction::new(vec![O1, O2], payments, change, None, fee)` succeeds — nothing validates the offset-vs-script binding there (send.rs:150-256).
4. `stx.multisig(&K)` iterates inputs; for `i = 1`, `p2tr_script_buf(K.offset(d2).group_key())` returns `None` (mod.rs:80-83), the `?` at send.rs:277 aborts, and `multisig` returns `None`.
5. `O1` — a genuinely spendable output — can never be signed for, because the only signing path (`TransactionMachine` → `TransactionSignMachine` → `TransactionSignatureMachine`) is constructed all-or-nothing over the pooled input set. Repeating with `d2` yielding an even key but a `script_pubkey` that doesn't match `prevouts[1]` produces the same abort via `None?` at send.rs:278.