### Title
Deserialization path bypasses the script-ownership restriction, reporting unspendable outputs as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` defines an access restriction on which outputs the wallet may treat as its own: only outputs whose `script_pubkey` is registered in `scripts` (i.e., pays to `key + offset*G` as a valid even-Y P2TR output) are spendable. `scan_transaction` enforces this by only emitting a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns an offset. However, `ReceivedOutput::read` reconstructs the exact same type from untrusted bytes with no ownership check at all — the `offset`, `output` (including `script_pubkey`), and `outpoint` are deserialized verbatim and never cross-validated. The restriction is declared but not enforced on the deserialization path, which is the direct analog of Tomcat failing to enforce `ServletSecurity` annotations.

### Finding Description
`Scanner::register_offset` documents that offsets are security-critical ("Arbitrary offsets may introduce a script path into the output, allowing the output to be spent by satisfaction of an arbitrary script"). The script map in `Scanner` is the enforcement of that restriction. `scan_transaction` (lines 199-214) only yields `ReceivedOutput`s for registered scripts.

`ReceivedOutput::read` (lines 122-134) bypasses all of this:
- `offset` is any scalar read via `Secp256k1::read_F`, with no check that `key + offset*G` even corresponds to the `output.script_pubkey`.
- `output` is an arbitrary `TxOut`; its `script_pubkey` is never compared against the script the offset derives.
- `outpoint` is arbitrary bytes, so the `ReceivedOutput` can claim to be any UTXO — real or fabricated.

Nothing downstream distinguishes a scanned `ReceivedOutput` from a deserialized one. A caller that round-trips scan results through `serialize`/`read` (the documented intent of the write/read pair, which is otherwise dead code) — or that accepts `ReceivedOutput`s from a less-trusted component — will treat attacker-crafted outputs as owned and spendable.

### Impact Explanation
The accepted impact class is "funds reported received that are not spendable." An attacker supplies serialized `ReceivedOutput` bytes claiming ownership of an output that either (a) does not exist, (b) pays to someone else's script, or (c) pays to a script with an attacker-chosen offset embedding a spendable script path (the exact hazard `register_offset` warns about). The consumer credits these as wallet funds. In case (c), or where the output exists but is not the scanner's, the signer will produce a signature for a spend the network rejects — or worse, the offset/scalar the attacker chose is mixed into the real group key's signing session, binding an attacker-chosen scalar into a signature over attacker-chosen data.

### Likelihood Explanation
Reachability requires a data flow where `ReceivedOutput::serialize`/`read` crosses a trust boundary (scanner output stored in a DB or relayed to a signer/orchestrator). The write/read API exists precisely for that hand-off, so the path is plausible, but it is not exercised inside this repo's in-scope code — hence Medium rather than High. The attacker needs only to supply bytes to `ReceivedOutput::read`; no key material or collusion is required.

### Recommendation
Bind the deserialized fields together. Either make `ReceivedOutput::read` take the `Scanner` (or the base key and its registered script set) and re-derive the expected `script_pubkey` from `offset`, rejecting mismatches; or drop the standalone `read`/`serialize` API and require all `ReceivedOutput`s to originate from `scan_transaction`/`scan_block`. At minimum, recompute `p2tr_script_buf(key + GENERATOR * offset)` and compare it to `output.script_pubkey` on read.

### Proof of Concept
```rust
// Attacker crafts a ReceivedOutput for an outpoint/script the scanner never saw
let mut bytes = Vec::new();
// arbitrary offset, e.g. an offset that embeds an attacker-controlled script path
bytes.extend(attacker_offset.to_bytes());
// TxOut paying to an arbitrary script_pubkey (not the scanner's registered script)
bytes.extend(serialize(&TxOut { value: Amount::from_sat(1_000_000),
                                script_pubkey: attacker_script }));
// fabricated or victim's outpoint
bytes.extend(serialize(&OutPoint::new(txid, 0)));

let claimed = ReceivedOutput::read(&mut &bytes[..]).unwrap();
// `claimed` is now indistinguishable from a scanner-produced output and is
// reported as spendable funds despite failing the Scanner's ownership check
```
The fix-side check that is missing: `debug_assert`/equality between `output.script_pubkey` and `p2tr_script_buf(key + GENERATOR * offset)`, which `scan_transaction` effectively guarantees but `read` does not.

Note: I could not fully trace every consumer of `ReceivedOutput` (processor-side spend construction is out of scope for this scan), so the severity rests on the read API being used across a trust boundary as designed.