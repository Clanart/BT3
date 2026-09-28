### Title
`Scanner::register_offset` lets a prior registrant permanently block a later offset registration, DoSing deposit recognition for that key - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::register_offset` derives a Taproot script for `key + offset*G`, incrementing the offset until the point has an even Y, then refuses the registration if the resulting `script_pubkey` is already present in `self.scripts` (`contains_key` → `None`). Because the increment-to-even step is deterministic, the *effective* offset a caller obtains depends on which offsets were registered earlier. A party who registers an offset first can therefore cause a later, distinct requested offset to resolve to the same script and be rejected, permanently preventing the scanner from attributing deposits to the intended sub-key.

### Finding Description
`register_offset` (networks/bitcoin/src/wallet/mod.rs:180-196) loops over `p2tr_script_buf(self.key + GENERATOR * offset)`; when `p2tr_script_buf` returns `None` (odd-Y point, lines 80-84) it does `offset += Scalar::ONE` and retries. Only after finding an even point does it check `self.scripts.contains_key(&script)` and bail with `None`.

This mirrors the pump-science bug: an attacker-observable/influenceable state check (whether the derived script is already registered — analogous to whether the ATA address already holds lamports) gates whether the "account" (script→offset registration) is created. The increment-before-check order makes the check manipulable:

- Suppose a legitimate flow intends to register offset `o` where `key + o*G` is odd. The scanner silently bumps to `o + 1`.
- If `o + 1` was already registered — by any earlier call, including one triggered by an attacker who supplies offsets or who can front-run a public/derivable offset sequence (e.g., counters derived from public data such as `H(key || index)`) — the legitimate registration returns `None`.
- The API offers no recovery path: `register_offset` returning `None` means the script will never be inserted, so `scan_transaction` (lines 199-214) will never emit a `ReceivedOutput` for funds sent to that derived address.

### Impact Explanation
Deposits sent to the address derived for the blocked offset are never reported by `scan_transaction`/`scan_block`. For an HDKD-style wallet where each deposit gets a fresh offset, the funds sit on-chain at a valid Taproot output the scanner refuses to track — a DoS of deposit recognition equivalent to the lock-pool DoS in the reference report (the created "account" is skipped, so the mechanism that consumes it fails). Note the offsets still control real keys; this is not a false-positive — the registration is dropped entirely.

### Likelihood Explanation
Reachable whenever offset registration is driven by publicly knowable or attacker-influenced inputs — e.g., sequential counters, or offsets derived from user-supplied deposit metadata (the documented surjectivity caveat acknowledges ordering matters but the failure mode is silent `None`, not an error the caller can distinguish from a genuine duplicate). An attacker who can cause `o + 1` (or any incremented landing point) to be registered before the victim's `o` blocks it deterministically. No threshold collusion, malicious validator, or leaked key is required — only the ability to trigger registrations with attacker-chosen offsets, which the API's doc warning ("offsets must be securely generated") does not enforce.

### Recommendation
- Return a distinct error for "script already registered" vs. "offset unusable" instead of `None`, so callers can retry with a fresh offset rather than silently dropping the deposit key.
- Alternatively, continue incrementing past a colliding script (with a bound and an explicit error), or store per-requested-offset identity so a collision on the *incremented* offset can be retried.
- At minimum, document that `None` may result from increment-collision by an earlier registrant and that callers must not treat `None` as benign.

### Proof of Concept
```rust
// Scanner::new(even_key)
let mut scanner = Scanner::new(key).unwrap();

// Legitimate flow will later request offset `o` where `key + o*G` is ODD,
// so register_offset(o) internally bumps to `o + 1`.

// Attacker (or any earlier registration) registers `o + 1` first:
// key + (o+1)*G is even, registration succeeds.
assert_eq!(scanner.register_offset(o_plus_1), Some(o_plus_1));

// Victim registers `o`:
//   p2tr_script_buf(key + o*G) -> None (odd)  => offset += 1
//   p2tr_script_buf(key + (o+1)*G) -> Some(script), but script already present
//   => None? -- registration permanently fails.
assert_eq!(scanner.register_offset(o), None);

// A Bitcoin tx paying to p2tr(x_only(key + o*G))... note the *original* o was odd so
// the intended address itself was never usable; the bumped o+1 script exists but is
// attributed to the attacker's offset, not the victim's intended sub-key.
```

The root cause is the ordering in `register_offset` (mutate offset to satisfaction of an external constraint, *then* existence-check and give up), exactly paralleling "check lamports → skip create" — the existence check is evaluated against state another party could have already populated.