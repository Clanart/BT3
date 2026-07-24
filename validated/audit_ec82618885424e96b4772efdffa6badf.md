### Title
`ProtocolParamset` Validation Does Not Check `num_signed_kickoffs <= num_kickoffs_per_round`, Causing Permanent Deposit Liveness Failure — (`crates/clementine-config/src/protocol.rs`, `core/src/config/protocol.rs`)

---

### Summary

`ProtocolParamset::from_toml_file` and `ProtocolParamset::from_env` validate each parameter individually (only `finality_depth >= 1` is checked as a cross-field invariant) but do not validate the required relationship `num_signed_kickoffs <= num_kickoffs_per_round`. When this invariant is violated, `get_kickoff_utxos_to_sign` silently returns fewer indices than expected, and the verifier's strict count check permanently rejects every deposit signing session, making the bridge completely non-functional for deposits.

---

### Finding Description

`ProtocolParamset` contains two related fields:

- `num_kickoffs_per_round`: total kickoff UTXOs created per round transaction
- `num_signed_kickoffs`: how many of those UTXOs are selected and signed per deposit

The protocol requires `num_signed_kickoffs <= num_kickoffs_per_round`. This relationship is documented in the field comment but is never enforced in code.

**Validation gap:**

`ProtocolParamset::from_toml_file` only checks `finality_depth >= 1`:

```rust
if paramset.finality_depth < 1 {
    return Err(BridgeError::ConfigError(
        "Finality depth must be at least 1".to_string(),
    ));
}
Ok(paramset)
```

`ProtocolParamset::from_env` has the same single check. Neither function validates `num_signed_kickoffs <= num_kickoffs_per_round`.

**Silent truncation in `get_kickoff_utxos_to_sign`:**

```rust
let mut numbers: Vec<usize> = (0..paramset.num_kickoffs_per_round).collect();
numbers.shuffle(&mut rng);
numbers.into_iter().take(paramset.num_signed_kickoffs).collect()
```

When `num_signed_kickoffs > num_kickoffs_per_round`, `numbers` has only `num_kickoffs_per_round` elements. `.take(num_signed_kickoffs)` silently returns only `num_kickoffs_per_round` elements — no panic, no error.

**Strict count enforcement in the verifier:**

```rust
if kickoff_txids[operator_idx][round_idx].len()
    != self.config.protocol_paramset().num_signed_kickoffs
{
    return Err(eyre::eyre!(
        "Number of signed kickoff utxos for operator: {}, round: {} is wrong. \
         Expected: {}, got: {}",
        operator_xonly_pk, round_idx,
        self.config.protocol_paramset().num_signed_kickoffs,
        kickoff_txids[operator_idx][round_idx].len()
    ).into());
}
```

The verifier expects exactly `num_signed_kickoffs` signed kickoff txids per round. When the operator produces only `num_kickoffs_per_round` (< `num_signed_kickoffs`), this check always fails, and the deposit is rejected.

---

### Impact Explanation

If `num_signed_kickoffs > num_kickoffs_per_round` is deployed:

1. Every deposit signing session fails at the verifier's count check.
2. No deposit can ever be finalized — the bridge is permanently non-functional for peg-in.
3. Users' BTC locked in the deposit address cannot be moved to the vault.
4. The `ProtocolParamset` is loaded at startup and baked into pre-signed transaction graphs; it cannot be changed without a full redeployment and re-signing of all transactions.
5. Any BTC already sent to deposit addresses before the misconfiguration is discovered is effectively stranded until redeployment.

This is a permanent liveness failure for the bridge deposit path.

---

### Likelihood Explanation

The default values (`num_kickoffs_per_round = 10`, `num_signed_kickoffs = 2`) are safe. However:

- The `scripts/run.sh` default sets `NUM_KICKOFFS_PER_ROUND=100` and `NUM_SIGNED_KICKOFFS=5` — safe.
- An operator tuning for higher security (more signed kickoffs) or lower resource usage (fewer kickoffs per round) could easily invert the relationship.
- There is no startup warning, no config-time error, and no runtime error until the first deposit signing session reaches the verifier count check.
- The failure is silent at configuration time and only manifests deep in the deposit flow.

---

### Recommendation

Add a cross-field validation in both `ProtocolParamset::from_toml_file` and `ProtocolParamset::from_env` in `crates/clementine-config/src/protocol.rs` and `core/src/config/protocol.rs`:

```rust
if paramset.num_signed_kickoffs > paramset.num_kickoffs_per_round {
    return Err(BridgeError::ConfigError(format!(
        "num_signed_kickoffs ({}) must be <= num_kickoffs_per_round ({})",
        paramset.num_signed_kickoffs, paramset.num_kickoffs_per_round
    )));
}
if paramset.num_signed_kickoffs == 0 {
    return Err(BridgeError::ConfigError(
        "num_signed_kickoffs must be at least 1".to_string(),
    ));
}
if paramset.num_kickoffs_per_round == 0 {
    return Err(BridgeError::ConfigError(
        "num_kickoffs_per_round must be at least 1".to_string(),
    ));
}
```

Also add this check to `BridgeConfig::check_general_requirements` so it is enforced at startup for all configuration paths.

---

### Proof of Concept

**Configuration:**
```
NUM_KICKOFFS_PER_ROUND=5
NUM_SIGNED_KICKOFFS=10
```

**Step 1:** `ProtocolParamset::from_env()` accepts this without error. [1](#0-0) 

**Step 2:** Operator calls `get_kickoff_utxos_to_sign` with this paramset. `numbers = [0,1,2,3,4]` (5 elements). `.take(10)` returns 5 elements, not 10. [2](#0-1) 

**Step 3:** The operator produces 5 signed kickoff txids per round. The verifier checks `5 != 10` and returns an error, rejecting the deposit. [3](#0-2) 

**Step 4:** Every subsequent deposit attempt hits the same error. The bridge is permanently non-functional for deposits until redeployment with corrected parameters.

The only validation that exists in both loading paths is the `finality_depth` check; no relationship between `num_signed_kickoffs` and `num_kickoffs_per_round` is enforced anywhere. [4](#0-3) [1](#0-0)

### Citations

**File:** core/src/config/protocol.rs (L120-127)
```rust
        if config.finality_depth < 1 {
            return Err(BridgeError::ConfigError(
                "Finality depth must be at least 1".to_string(),
            ));
        }

        Ok(config)
    }
```

**File:** core/src/builder/transaction/sign.rs (L69-75)
```rust
    let mut numbers: Vec<usize> = (0..paramset.num_kickoffs_per_round).collect();
    numbers.shuffle(&mut rng);

    numbers
        .into_iter()
        .take(paramset.num_signed_kickoffs)
        .collect()
```

**File:** core/src/verifier.rs (L1308-1314)
```rust
                if kickoff_txids[operator_idx][round_idx].len()
                    != self.config.protocol_paramset().num_signed_kickoffs
                {
                    return Err(eyre::eyre!(
                        "Number of signed kickoff utxos for operator: {}, round: {} is wrong. Expected: {}, got: {}",
                                operator_xonly_pk, round_idx, self.config.protocol_paramset().num_signed_kickoffs, kickoff_txids[operator_idx][round_idx].len()
                    ).into());
```

**File:** crates/clementine-config/src/protocol.rs (L134-145)
```rust
    pub fn from_toml_file(path: &Path) -> Result<Self, BridgeError> {
        let contents = fs::read_to_string(path).wrap_err("Failed to read config file")?;

        let paramset: Self = toml::from_str(&contents).wrap_err("Failed to parse TOML")?;
        if paramset.finality_depth < 1 {
            return Err(BridgeError::ConfigError(
                "Finality depth must be at least 1".to_string(),
            ));
        }

        Ok(paramset)
    }
```
