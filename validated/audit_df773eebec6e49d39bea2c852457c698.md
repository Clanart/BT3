### Title
Unbounded, attacker-controlled allocation in `ThresholdKeys::read` before parameter validation - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` deserializes the multisig size `n` directly from untrusted bytes (a raw `u16` up to 65535) and immediately allocates `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficients, then loops `n` times performing `C::read_G` into a `HashMap` — all before `ThresholdParams::new` / `ThresholdKeys::new` validate `t`, `n`, and `i`. This mirrors CVE-2016-6173: a declared length/count controls resource consumption prior to any validation of whether the structure is legitimate. [1](#0-0) [2](#0-1) 

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`):

1. After the curve-ID check, `t`, `n`, and `i` are read as attacker-controlled `u16`s (lines 591–602). `Participant::new` bounds `i`, but `t` and `n` are unbounded at this point.
2. If the interpolation tag is `0` (`Constant`), `Vec::with_capacity(usize::from(n))` is executed at line 608 — a ~2 MB allocation (65535 × `size_of::<C::F>()`, e.g. 32 bytes for a Ristretto/ed25519 scalar) triggered by a handful of attacker bytes, regardless of how much data actually follows.
3. Lines 620–623 then build a `HashMap` of `n` verification shares, calling `<C as Ciphersuite>::read_G` per entry. With a fully populated payload the attacker can force ~65535 group-element decodes plus map overhead (~4–8 MB retained) per single message.
4. Only at line 625–626 does `ThresholdParams::new(t, n, i)` get a chance to reject the parameters — the allocations and the `n`-element read loop have already occurred.

`ThresholdKeys::read` is an explicitly in-scope untrusted-byte sink. The same pattern (claim-a-count → allocate/read proportional to count, validate afterwards) is exactly the NSD zone-transfer flaw: resource expenditure is governed by attacker-declared quantity rather than by any enforced bound tied to the real multisig.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (e.g., key material or DKG messages relayed through the coordinator/substrate path) can cause repeated multi-megabyte allocations and tens of thousands of point decompression operations per message, while sending only a tiny malformed prefix (interpolation tag `0`, `n = 0xffff`, then truncated input). The `Vec::with_capacity` alone yields roughly 2 MB of heap allocation per ~10 input bytes, a large amplification factor; repeated messages produce sustained allocator churn and memory pressure that can crash or starve the node — the same denial-of-service shape as the NSD `/tmp`-exhaustion/crash.

### Likelihood Explanation
The read path requires no key knowledge, no valid signature, and no threshold collusion — only delivery of attacker-controlled bytes to the `read` entry point. The amplification (declared `u16` count → immediate pre-validation allocation and read loop) is deterministic and repeatable. Severity is bounded per-call (~2–8 MB, transient on early error), so this rates Medium rather than High: it is an exhaustion/churn DoS, not secret leakage or forgery.

### Recommendation
- Validate `t`/`n`/`i` against sane bounds (e.g., `n <= MAX_PARTICIPANTS`, `t <= n`) immediately after reading them, before any allocation sized by `n`.
- Replace `Vec::with_capacity(n)` with incremental `push` (allocation grows only as real bytes arrive), or cap `n` at the maximum feasible participant count.
- Bound the verification-share `HashMap` loop the same way, and consider a byte-budget check (e.g., compare remaining input length against `n * encoded_point_size`) before reading.

### Proof of Concept
For `C = Ristretto`, `ThresholdKeys::read` consumes:

```
[len(C::ID)=4B][C::ID bytes][t:u16][n:u16=0xffff][i:u16][interp tag=0x00]
```

With interpolation tag `0`, line 608 executes `Vec::with_capacity(65535)` — allocating ~2 MB for coefficient scalars — before `read_F` fails on the truncated stream and before `ThresholdParams::new` ever runs. Repeating this minimal message forces continuous multi-megabyte heap allocation per few input bytes; supplying the full `65535 × 32 B` scalar payload plus `65535` encoded points additionally forces ~65535 `read_G` decompressions and a ~4–8 MB `HashMap` per message, all pre-validation.

Note: I did not find a stronger in-scope analog. Other unbounded `vec![0; u32]`-style reads exist (`InInstruction::read` in `networks/ethereum/src/router.rs:93`, `Payment::read` in `processor/src/plan.rs:56,65`, `PlanFromScanning::read` in `processor/src/multisigs/db.rs:34`), but those crates are outside the stated scope. The coordinator's tributary `Transaction::read` does enforce `TRANSACTION_SIZE_LIMIT` on its commitment vectors, and the libp2p codec enforces `MAX_LIBP2P_REQRES_MESSAGE_SIZE`, so they are not vulnerable. I was unable to fully inspect `EncryptedMessage::read` in `crypto/dkg/pedpop` or `ReceivedOutput::read` in `networks/bitcoin` within the available iterations; if either contains an attacker-controlled length prefix feeding `vec![0; len]`, it would be an equivalent or stronger instance of this same class.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-613)
```rust
    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
```

**File:** crypto/dkg/src/lib.rs (L620-631)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```
