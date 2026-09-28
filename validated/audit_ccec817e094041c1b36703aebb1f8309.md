### Title
Spending of a legitimately received output is silently aborted by a redundant parity/script re-derivation check in `SignableTransaction::multisig` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the UXD report (redeem re-checks whitelist and locks funds), `SignableTransaction::multisig` re-derives the output script from `keys.offset(offset).group_key()` via `p2tr_script_buf` and silently returns `None` ("wrong keys") when the derivation fails — even though the `ReceivedOutput` was already validated at scan time when `Scanner::scan_transaction` matched its `script_pubkey` against registered scripts.

### Finding Description
The receive path validates an output in `Scanner::scan_transaction` by matching `output.script_pubkey` against the `scripts` map (`networks/bitcoin/src/wallet/mod.rs:199-214`). A `ReceivedOutput` is also constructible from raw bytes via `ReceivedOutput::read` (`mod.rs:122-134`), which accepts any `Scalar` offset and any `TxOut`/`OutPoint` — the offset is never checked against the script at read time.

At spend time, `SignableTransaction::multisig` performs a second validation (`send.rs:273-285`):

```rust
let offset = keys.clone().offset(self.offsets[i]);
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
```

Two failure modes lock the funds:

1. `p2tr_script_buf` returns `None` whenever `offset.group_key()` is odd (`mod.rs:80-83`). If the offset attached to the `ReceivedOutput` does not correspond to the registered (even) offset for that script — e.g., it was incremented by `register_offset`'s parity loop (`mod.rs:184-194`) and a stale/un-mutated offset was persisted, or it came from untrusted bytes via `ReceivedOutput::read` — the whole multisig returns `None` and no signature is produced for *any* input of the transaction.
2. Because the check uses `?` on `Option` inside the constructor, a single inconsistent input aborts the entire `SignableTransaction`, not just the offending input.

The scanner only ever stores scripts for even keys, so the registered offset always satisfies the check — but nothing binds a `ReceivedOutput`'s `offset` field to the script after serialization/deserialization. The invariant is enforced twice at different layers with different representations (script map at receive time, scalar arithmetic at spend time), and divergence permanently blocks signing for the UTXO rather than surfacing a recoverable error.

### Impact Explanation
A `ReceivedOutput` whose stored offset no longer yields the even tweaked key matching its `script_pubkey` can never be spent through `TransactionMachine`: `multisig` returns `None`, `preprocess` is never reached, and the UTXO's funds remain locked under the threshold key — the same "collateral stuck in depository" outcome as the reference report. Since the failure is `None` rather than a typed error, callers cannot distinguish "wrong keys" from "unspendable output" to attempt recovery.

### Likelihood Explanation
Reachability requires an offset/script mismatch in a `ReceivedOutput`. The public `read` path accepts arbitrary bytes, and offset mutation in `register_offset` makes persisted-but-unmutated offsets a realistic integration hazard (the API returns the used offset; a caller storing the requested offset produces exactly this divergence whenever the parity loop incremented). Because offsets are surjective per the docstring, order-of-registration changes can also desynchronize a persisted offset from the script map. Likelihood is moderate: the scanner self-consistent path works, but any persisted/foreign `ReceivedOutput` can hit it.

### Recommendation
Either (a) validate the offset↔script binding at `ReceivedOutput::read`/construction time so spend-time cannot diverge, or (b) make `multisig` return a typed error (or skip only the inconsistent input) so a single bad input does not freeze the entire transaction, matching the reference report's recommendation to not re-gate the exit path on an entry-time condition.

### Proof of Concept
```rust
// key: even tweaked group key; scanner registered offset `o` for some script
let output = scanner.scan_transaction(&tx).pop().unwrap(); // offset = o (even point)

// Simulate a deserialized output carrying the *requested* offset o-1 (odd point),
// as produced by ReceivedOutput::read over persisted/unmutated bytes
let mut buf = Vec::new();
let mut bad = output.clone();
// overwrite offset via serialize: write (o-1) then same TxOut/OutPoint
bad.write(&mut Vec::new()).unwrap();
let raw = {
  let mut b = bad.serialize();
  b[..32].copy_from_slice(&(output.offset() - Scalar::ONE).to_bytes());
  b
};
let bad = ReceivedOutput::read(&mut raw.as_slice()).unwrap();

let stx = SignableTransaction::new(vec![bad], &payments, None, None, fee).unwrap();
// Silently returns None: key + (o-1)*G is odd -> p2tr_script_buf is None
assert!(stx.multisig(&keys).is_none()); // funds unspendable via this API
```

Uncertainty: whether any in-tree caller feeds `ReceivedOutput::read` from an untrusted or mutable-offset source could not be fully confirmed within the iteration limit; the finding stands on the divergence between the two validation layers and the silent `None` abort.