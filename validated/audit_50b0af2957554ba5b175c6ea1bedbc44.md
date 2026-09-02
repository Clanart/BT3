### No vulnerability found for this question.

**Reasoning:** `digest_to_decimal` in `bridge-circuit-host/src/utils.rs` does not hash or concatenate any protocol structures at all — it is a pure format-conversion helper. It receives an already-computed, fixed-size `Risc0Digest` (8 x 32-bit words) that was sliced out of the STARK seal at a position dictated by `K_SEAL_TYPES`/`DIGEST_WORDS` in `to_json`, and simply maps it to a BN254 scalar via risc0's own canonical `digest_to_fr`, then formats the result as a decimal string for the Circom witness generator. [1](#0-0) [2](#0-1) 

There is no attacker-controlled concatenation of "mid-state txid vs internal node," journal fields, or the "module's input struct" happening inside this function — those hashing/serialization steps (if they exist) occur elsewhere, in the actual risc0 guest circuit and in `circuits-lib::bridge_circuit`, not in this converter. The digest passed in is already a completed 32-byte risc0 digest extracted from a well-defined position in the seal transcript, not an attacker-assembled byte blob being hashed here. Since the function performs no hashing of distinguishable protocol facts, there is no domain-separation invariant for this code to violate, and no injectivity claim about `digest_to_decimal` itself is meaningful — `digest_to_fr` is risc0's own standard reduction of a digest into `Fr`, used unmodified by this repo.

The premise of the question — that this specific function hashes two different structures into one digest space — does not match what the code does, so the described exploit path is not present at this file/function.

### Citations

**File:** bridge-circuit-host/src/utils.rs (L360-376)
```rust
                if pos + DIGEST_WORDS > iop.len() {
                    return Err(eyre!(
                        "Not enough data for digest at position {}: need {} words, have {} remaining",
                        pos,
                        DIGEST_WORDS,
                        iop.len() - pos
                    ));
                }
                let digest = Risc0Digest::try_from(&iop[pos..pos + DIGEST_WORDS])
                    .with_context(|| format!("Failed to create digest at position {pos}"))?;
                let value = digest_to_decimal(&digest).with_context(|| {
                    format!("Failed to convert digest to decimal at index {index}")
                })?;
                pos += 8;
                writeln!(writer, "    \"{value}\"")
                    .with_context(|| format!("Failed to write digest value at index {index}",))?;
            }
```

**File:** bridge-circuit-host/src/utils.rs (L384-394)
```rust
fn digest_to_decimal(digest: &Risc0Digest) -> Result<String> {
    to_decimal(&format!("{:?}", digest_to_fr(digest)))
        .ok_or_else(|| eyre!("Failed to convert digest to decimal format"))
}

fn to_decimal(s: &str) -> Option<String> {
    s.strip_prefix("Fr(0x")
        .and_then(|s| s.strip_suffix(')'))
        .and_then(|stripped| BigUint::from_str_radix(stripped, 16).ok())
        .map(|n| n.to_str_radix(10))
}
```
