### Title
Attacker-controlled `t`/`n` in `ThresholdKeys::read` triggers O(t²) Lagrange interpolation during group-key derivation - (File: crypto/dkg/src/lib.rs)

### Summary
CVE-2019-13011 is an excessive-algorithmic-complexity bug reachable with low-privilege input. The analog in Serai is `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), which deserializes `t`, `n`, and `i` as untrusted `u16` fields, then calls `ThresholdKeys::new`, which computes the group key by calling `interpolation_factor` once per participant in `1..=t`. For `Interpolation::Lagrange`, each `interpolation_factor` call iterates over all `t` included participants (`crypto/dkg/src/lib.rs:229-247`), producing O(t²) scalar multiplications. With attacker-supplied bytes setting `t = n = u16::MAX = 65535`, deserialization performs ~4.3 billion field multiplications — a severe CPU denial of service triggered by a small serialized blob, with no signature verification or secret access required beforehand.

### Finding Description
`ThresholdKeys::read` parses header bytes (curve ID, `t`, `n`, `i`, interpolation tag, `n` scalars/points) from an arbitrary `io::Read` and hands them to `ThresholdKeys::new`. `ThresholdParams::new` only enforces `0 < t <= n` and `i <= n` (`crypto/dkg/src/lib.rs:166-178`), so `t` and `n` may each be as large as 65535. `ThresholdKeys::new` then computes:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
``` [1](#0-0) 

`Interpolation::Lagrange`'s `interpolation_factor` loops over the whole `included` slice accumulating numerator and denominator: [2](#0-1) 

The product is quadratic: t group scalar-multiplications, each preceded by an O(t) scalar loop. The same quadratic blow-up exists in `ThresholdKeys::view`, which calls `interpolation_factor` once per included signer (`crypto/dkg/src/lib.rs:500-507`), but `read` is the stronger path because it is reached purely by untrusted bytes before any authorization.

### Impact Explanation
An unprivileged party who can supply serialized `ThresholdKeys` to a service (anywhere `ThresholdKeys::read` consumes network- or peer-provided bytes, e.g. key-share exchange, coordinator messages, or stored data) forces the victim to execute ~t² ≈ 4.3×10⁹ field multiplications plus 65535 group scalar multiplications, on a serialized input of only ~2 MiB (65535 encoded points). This hangs the caller's thread for a disproportionate amount of work — the exact "excessive algorithmic complexity" class of CVE-2019-13011. If `read` is invoked on a critical path (consensus processor, coordinator, or per-message handling), this is a repeatable, low-cost remote DoS of a validator/processor. DoS of honest nodes in a threshold network also stalls signing/liveness, which is at least Medium impact.

### Likelihood Explanation
Reachability requires only that some deployment feeds externally influenced bytes into `ThresholdKeys::read` — a listed in-scope untrusted-input entry point. The blob is trivially constructible (no proofs, signatures, or secrets needed; `verification_shares` can be the identity/generator repeated). Cost to the attacker is a ~2 MiB payload; cost to the victim is billions of field operations. Likelihood is moderate-to-high wherever the serialized keys are not fully trusted; lower if keys are only ever self-generated in-process.

### Recommendation
- Bound `t`/`n` to a deployment-realistic maximum (e.g. a few hundred) inside `ThresholdParams::new` or at least inside `ThresholdKeys::read` before doing any interpolation work.
- Compute Lagrange coefficients more efficiently: accumulate the numerator product once (product of all included indexes), then derive each factor via one inversion per participant, reducing `new`/`view` from O(t²) to O(t) inversions plus O(t) group work.
- Alternatively precompute per-participant factors once in `view`/`new` and reuse them for both the secret share and all verification shares instead of recomputing per signer.

### Proof of Concept
Conceptual trigger (no full runtime needed):

```rust
use std::io::Cursor;
use frost::curve::Secp256k1;
use frost::dkg::{ThresholdKeys, Participant};

// Craft bytes: ID len/ID, t = n = 65535, i = 1, interpolation = Lagrange (1),
// secret_share = 1, followed by n encoded generator points.
let mut buf = Vec::new();
buf.extend(9u32.to_le_bytes());
buf.extend(b"Secp256k1"); // matching C::ID
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend([1u8; 32]);              // secret_share (canonical 1)
for _ in 0..65535 {
    buf.extend(/* compressed generator bytes */);
}

// This call performs ~65535^2 ≈ 4.3 billion scalar multiplications inside
// Interpolation::Lagrange::interpolation_factor via ThresholdKeys::new.
let _ = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(buf));
```

The work happens in `ThresholdKeys::new` → `interpolation_factor` (`crypto/dkg/src/lib.rs:376-378` and `232-247`), gated only by `t <= n <= u16::MAX` (`crypto/dkg/src/lib.rs:171-175`). No other validation bounds the quadratic loop.

### Citations

**File:** crypto/dkg/src/lib.rs (L232-247)
```rust
        let mut num = F::ONE;
        let mut denom = F::ONE;
        for l in included {
          if i == *l {
            continue;
          }

          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
      }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```
