### Title
Missing Validation of Critical `ProtocolParamset` Parameters Enables Zero-BTC Deposit Acceptance and Challenge Window Collapse — (`crates/clementine-config/src/protocol.rs`, `core/src/config/protocol.rs`, `core/src/config/mod.rs`)

---

### Summary

`ProtocolParamset`, the consensus-critical configuration struct that governs every bridge transaction, is loaded from TOML files or environment variables with only a single guard: `finality_depth >= 1`. All other security-critical fields — `bridge_amount`, `collateral_funding_amount`, `operator_challenge_amount`, and every timelock (`disprove_timeout_timelock`, `operator_challenge_timeout_timelock`, `watchtower_challenge_timeout_timelock`, `assert_timeout_timelock`, `operator_reimburse_timelock`, `num_round_txs`, `num_signed_kickoffs`) — are accepted at any value including zero. Because the paramset is loaded once at startup and treated as immutable for the lifetime of the bridge session, a misconfigured zero value produces a broken invariant that cannot be corrected without a full restart and re-signing ceremony.

---

### Finding Description

**Root cause — `ProtocolParamset::from_toml_file` and `ProtocolParamsetExt::from_env`**

Both loading paths construct the struct and then apply exactly one check:

```rust
// crates/clementine-config/src/protocol.rs  line 138
if paramset.finality_depth < 1 {
    return Err(BridgeError::ConfigError(
        "Finality depth must be at least 1".to_string(),
    ));
}
```

```rust
// core/src/config/protocol.rs  line 120
if config.finality_depth < 1 {
    return Err(BridgeError::ConfigError(
        "Finality depth must be at least 1".to_string(),
    ));
}
```

`check_general_requirements`, called unconditionally in `main.rs` at startup, adds only two more checks (genesis hash and `start_height >= genesis_height`). It does not validate any amount or timelock field.

The following fields have **no lower-bound guard** in any code path:

| Field | Dangerous zero-value effect |
|---|---|
| `bridge_amount` | Verifier accepts 0-sat deposits as valid |
| `collateral_funding_amount` | Operators fund round-tx chain with 0 BTC |
| `operator_challenge_amount` | Challenge tx pays 0 sat to operator |
| `disprove_timeout_timelock` | Disprove window collapses to 0 blocks |
| `operator_challenge_timeout_timelock` | Challenge window collapses to 0 blocks |
| `watchtower_challenge_timeout_timelock` | Watchtower window collapses to 0 blocks |
| `assert_timeout_timelock` | Assert window collapses to 0 blocks |
| `num_round_txs` | `get_num_required_nofn_sigs` returns 0 |
| `num_signed_kickoffs` | `get_kickoff_utxos_to_sign` returns empty vec |

**Concrete impact path 1 — `bridge_amount = 0`**

The verifier's deposit validation at `core/src/verifier.rs:688` is:

```rust
if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount {
    return Err(BridgeError::InvalidDeposit(reason));
}
```

With `bridge_amount = 0` this becomes `0 != 0 → false`, so any UTXO carrying 0 satoshis at the expected Taproot script pubkey passes the check. In `bridge_nonstandard = true` mode (the default for regtest and testnet4 deployments), 0-sat outputs are explicitly supported by `default_utxo_amount()` returning `Amount::from_sat(0)`. A user can therefore construct a 0-sat output at the bridge address, have it accepted as a valid deposit by all verifiers, and trigger the move-to-vault flow — minting bridged BTC on Citrea without depositing any real BTC.

**Concrete impact path 2 — timelock parameters = 0**

The challenge, disprove, and assert timelocks are embedded directly into Bitcoin script via `Sequence::from_height(paramset.operator_reimburse_timelock)` and equivalent calls in the transaction builder. A zero value produces a `Sequence` of 0 blocks, meaning the corresponding spend path is immediately available. With `disprove_timeout_timelock = 0` and `operator_challenge_timeout_timelock = 0`, a malicious operator can:

1. Broadcast a kickoff transaction.
2. Immediately spend the assert-timeout path (0-block timelock) before any watchtower can challenge.
3. Claim the reimbursement without ever posting a valid BitVM proof.

The challenge-response fraud-proof mechanism that protects all bridged BTC is entirely neutralised.

**Concrete impact path 3 — `num_round_txs = 0`**

`get_num_required_nofn_sigs` multiplies by `num_round_txs`:

```rust
deposit_data.get_num_operators()
    * self.protocol_paramset().num_round_txs   // = 0
    * self.protocol_paramset().num_signed_kickoffs
    * self.get_num_required_nofn_sigs_per_kickoff(deposit_data)
```

The result is 0, so the verifier expects zero N-of-N signatures. The deposit signing ceremony completes immediately with no cryptographic commitment from any verifier, breaking the N-of-N covenant that is the bridge's core security primitive.

---

### Impact Explanation

- **`bridge_amount = 0`**: Verifier accepts 0-sat deposits; users can mint bridged BTC on Citrea without depositing real BTC — direct theft of bridge-issued value.
- **Timelock = 0**: Challenge windows collapse; malicious operators can claim reimbursements and escape disprove — permanent loss of bridged BTC and operator collateral.
- **`num_round_txs = 0`**: N-of-N signing requirement drops to zero; deposits are finalised without any verifier signature — complete bypass of the trust-minimised covenant.
- **`collateral_funding_amount = 0`**: Operators post no collateral; there is nothing to slash when fraud is detected — slashable exposure of operator collateral is eliminated.

---

### Likelihood Explanation

The `ProtocolParamset` is loaded from a TOML file or environment variables at every actor startup. The parameters are effectively immutable for the bridge session lifetime (changing them invalidates all pre-signed transactions). A single typo — e.g., `bridge_amount = 0` instead of `bridge_amount = 1000000000`, or omitting a timelock variable so it defaults to 0 — silently produces a broken bridge. The `.env.example` file already ships with `KICKOFF_AMOUNT=0`, demonstrating that zero-value parameters are considered normal by the tooling, increasing the risk that an operator copies this pattern for other fields. No runtime warning or startup error is emitted for any of the dangerous zero values listed above.

---

### Recommendation

Add a `validate()` method to `ProtocolParamset` (in `crates/clementine-config/src/protocol.rs`) and call it from both `from_toml_file` and `from_env`, as well as from `check_general_requirements`. At minimum enforce:

```rust
fn validate(&self) -> Result<(), BridgeError> {
    if self.bridge_amount == Amount::ZERO {
        return Err(BridgeError::ConfigError("bridge_amount must be > 0".into()));
    }
    if self.collateral_funding_amount == Amount::ZERO {
        return Err(BridgeError::ConfigError("collateral_funding_amount must be > 0".into()));
    }
    if self.operator_challenge_amount == Amount::ZERO {
        return Err(BridgeError::ConfigError("operator_challenge_amount must be > 0".into()));
    }
    if self.num_round_txs == 0 {
        return Err(BridgeError::ConfigError("num_round_txs must be > 0".into()));
    }
    if self.num_kickoffs_per_round == 0 {
        return Err(BridgeError::ConfigError("num_kickoffs_per_round must be > 0".into()));
    }
    if self.num_signed_kickoffs == 0 || self.num_signed_kickoffs > self.num_kickoffs_per_round {
        return Err(BridgeError::ConfigError(
            "num_signed_kickoffs must be in (0, num_kickoffs_per_round]".into()
        ));
    }
    // All challenge/disprove/assert timelocks must be >= minimum safe value
    let min_timelock = 1u16;
    for (name, val) in [
        ("disprove_timeout_timelock", self.disprove_timeout_timelock),
        ("operator_challenge_timeout_timelock", self.operator_challenge_timeout_timelock),
        ("watchtower_challenge_timeout_timelock", self.watchtower_challenge_timeout_timelock),
        ("assert_timeout_timelock", self.assert_timeout_timelock),
    ] {
        if val < min_timelock {
            return Err(BridgeError::ConfigError(
                format!("{name} must be >= {min_timelock}")
            ));
        }
    }
    Ok(())
}
```

---

### Proof of Concept

**Scenario: `bridge_amount = 0` → free deposit**

1. Deploy all actors with `BRIDGE_AMOUNT=0` and `BRIDGE_NONSTANDARD=true`.
2. Create a Bitcoin transaction with a 0-sat output at the expected N-of-N Taproot address.
3. Submit the deposit outpoint to the aggregator's `new_deposit` RPC.
4. The verifier's `is_deposit_valid` at `core/src/verifier.rs:688` evaluates `0 != 0 → false` and passes.
5. The N-of-N signing ceremony completes; the operator broadcasts the move-to-vault transaction with 0 BTC.
6. Citrea credits the user with bridged BTC for a deposit of 0 real BTC.

**Scenario: `disprove_timeout_timelock = 0` → unchallenged fraud**

1. Deploy all actors with `DISPROVE_TIMEOUT_TIMELOCK=0` and `OPERATOR_CHALLENGE_TIMEOUT_TIMELOCK=0`.
2. Operator broadcasts a kickoff transaction with a fraudulent claim.
3. Because the timelock is 0 blocks, the assert-timeout spend path is immediately valid.
4. Operator spends the assert-timeout path in the same block as the kickoff, before any watchtower can react.
5. Operator collects the reimbursement; no disprove is possible; bridged BTC is stolen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/clementine-config/src/protocol.rs (L76-130)
```rust
pub struct ProtocolParamset {
    /// Bitcoin network to work on (mainnet, testnet, regtest).
    pub network: Network,
    /// Number of round transactions that the operator will create.
    pub num_round_txs: usize,
    /// Number of kickoff UTXOs per round transaction.
    pub num_kickoffs_per_round: usize,
    /// Number of kickoffs that are signed per round and deposit.
    /// There are num_kickoffs_per_round utxo's, but only num_signed_kickoffs are signed.
    pub num_signed_kickoffs: usize,
    /// Bridge deposit amount that users can deposit.
    pub bridge_amount: Amount,
    /// Amount allocated for each kickoff UTXO.
    pub kickoff_amount: Amount,
    /// Amount allocated for operator challenge transactions.
    pub operator_challenge_amount: Amount,
    /// Collateral funding amount for operators used to fund the round transaction chain.
    pub collateral_funding_amount: Amount,
    /// Length of the blockhash commitment in kickoff transactions.
    pub kickoff_blockhash_commit_length: u32,
    /// Total number of bytes of a watchtower challenge.
    pub watchtower_challenge_bytes: usize,
    /// Winternitz derivation log_d (shared for all WOTS commitments)
    /// Currently used in statics and thus cannot be different from [`WINTERNITZ_LOG_D`].
    pub winternitz_log_d: u32,
    /// Number of blocks after which user can take deposit back if deposit request fails.
    pub user_takes_after: u16,
    /// Number of blocks for operator challenge timeout timelock (currently BLOCKS_PER_WEEK)
    pub operator_challenge_timeout_timelock: u16,
    /// Number of blocks for operator challenge NACK timelock (currently BLOCKS_PER_WEEK * 3)
    pub operator_challenge_nack_timelock: u16,
    /// Number of blocks for disprove timeout timelock (currently BLOCKS_PER_WEEK * 5)
    pub disprove_timeout_timelock: u16,
    /// Number of blocks for assert timeout timelock (currently BLOCKS_PER_WEEK * 4)
    pub assert_timeout_timelock: u16,
    /// Number of blocks for latest blockhash timeout timelock (currently BLOCKS_PER_WEEK * 2.5)
    pub latest_blockhash_timeout_timelock: u16,
    /// Number of blocks for operator reimburse timelock (currently BLOCKS_PER_DAY * 2)
    /// Timelocks operator from sending the next Round Tx after the Ready to Reimburse Tx.
    pub operator_reimburse_timelock: u16,
    /// Number of blocks for watchtower challenge timeout timelock (currently BLOCKS_PER_WEEK * 2)
    pub watchtower_challenge_timeout_timelock: u16,
    /// Amount of depth a block should have from the current head to be considered finalized
    /// Also means finality depth, how many confirmations are needed for a block to be considered finalized
    /// The chain tip has 1 confirmation. Minimum value should be 1.
    pub finality_depth: u32,
    /// start height to sync the chain from, i.e. the height bridge was deployed
    pub start_height: u32,
    /// Genesis height to sync the header chain proofs from
    pub genesis_height: u32,
    /// Genesis chain state hash
    pub genesis_chain_state_hash: [u8; 32],
    /// Denotes if the bridge is non-standard, i.e. uses 0 sat outputs for round tx (except collateral) and kickoff outputs
    pub bridge_nonstandard: bool,
}
```

**File:** crates/clementine-config/src/protocol.rs (L132-145)
```rust
impl ProtocolParamset {
    /// Parse a `ProtocolParamset` from a TOML file.
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

**File:** core/src/config/protocol.rs (L62-127)
```rust
    fn from_env() -> Result<Self, BridgeError> {
        let config = ProtocolParamset {
            network: read_string_from_env_then_parse::<Network>("NETWORK")?,
            num_round_txs: read_string_from_env_then_parse::<usize>("NUM_ROUND_TXS")?,
            num_kickoffs_per_round: read_string_from_env_then_parse::<usize>(
                "NUM_KICKOFFS_PER_ROUND",
            )?,
            num_signed_kickoffs: read_string_from_env_then_parse::<usize>("NUM_SIGNED_KICKOFFS")?,
            bridge_amount: Amount::from_sat(read_string_from_env_then_parse::<u64>(
                "BRIDGE_AMOUNT",
            )?),
            kickoff_amount: Amount::from_sat(read_string_from_env_then_parse::<u64>(
                "KICKOFF_AMOUNT",
            )?),
            operator_challenge_amount: Amount::from_sat(read_string_from_env_then_parse::<u64>(
                "OPERATOR_CHALLENGE_AMOUNT",
            )?),
            collateral_funding_amount: Amount::from_sat(read_string_from_env_then_parse::<u64>(
                "COLLATERAL_FUNDING_AMOUNT",
            )?),
            kickoff_blockhash_commit_length: read_string_from_env_then_parse::<u32>(
                "KICKOFF_BLOCKHASH_COMMIT_LENGTH",
            )?,
            watchtower_challenge_bytes: read_string_from_env_then_parse::<usize>(
                "WATCHTOWER_CHALLENGE_BYTES",
            )?,
            winternitz_log_d: read_string_from_env_then_parse::<u32>("WINTERNITZ_LOG_D")?,
            user_takes_after: read_string_from_env_then_parse::<u16>("USER_TAKES_AFTER")?,
            operator_challenge_timeout_timelock: read_string_from_env_then_parse::<u16>(
                "OPERATOR_CHALLENGE_TIMEOUT_TIMELOCK",
            )?,
            operator_challenge_nack_timelock: read_string_from_env_then_parse::<u16>(
                "OPERATOR_CHALLENGE_NACK_TIMELOCK",
            )?,
            disprove_timeout_timelock: read_string_from_env_then_parse::<u16>(
                "DISPROVE_TIMEOUT_TIMELOCK",
            )?,
            assert_timeout_timelock: read_string_from_env_then_parse::<u16>(
                "ASSERT_TIMEOUT_TIMELOCK",
            )?,
            operator_reimburse_timelock: read_string_from_env_then_parse::<u16>(
                "OPERATOR_REIMBURSE_TIMELOCK",
            )?,
            watchtower_challenge_timeout_timelock: read_string_from_env_then_parse::<u16>(
                "WATCHTOWER_CHALLENGE_TIMEOUT_TIMELOCK",
            )?,
            finality_depth: read_string_from_env_then_parse::<u32>("FINALITY_DEPTH")?,
            start_height: read_string_from_env_then_parse::<u32>("START_HEIGHT")?,
            genesis_height: read_string_from_env_then_parse::<u32>("GENESIS_HEIGHT")?,
            genesis_chain_state_hash: convert_hex_string_to_bytes(
                &read_string_from_env_then_parse::<String>("GENESIS_CHAIN_STATE_HASH")?,
            )?,
            latest_blockhash_timeout_timelock: read_string_from_env_then_parse::<u16>(
                "LATEST_BLOCKHASH_TIMEOUT_TIMELOCK",
            )?,
            bridge_nonstandard: read_string_from_env_then_parse::<bool>("BRIDGE_NONSTANDARD")?,
        };

        if config.finality_depth < 1 {
            return Err(BridgeError::ConfigError(
                "Finality depth must be at least 1".to_string(),
            ));
        }

        Ok(config)
    }
```

**File:** core/src/config/mod.rs (L237-289)
```rust
    pub async fn check_general_requirements(&self) -> Result<(), BridgeError> {
        // check genesis state hash
        let rpc = ExtendedBitcoinRpc::connect(
            self.bitcoin_rpc_url.clone(),
            self.bitcoin_rpc_user.clone(),
            self.bitcoin_rpc_password.clone(),
            None,
        )
        .await
        .wrap_err("Failed to connect to Bitcoin RPC while checking general requirements")?;

        let genesis_chain_state = HeaderChainProver::get_chain_state_from_height(
            &rpc,
            self.protocol_paramset().genesis_height.into(),
            self.protocol_paramset().network,
        )
        .await
        .wrap_err("Failed to get genesis chain state while checking general requirements")?;

        let mut reasons = Vec::new();

        if genesis_chain_state.to_hash() != self.protocol_paramset().genesis_chain_state_hash {
            reasons.push(format!(
                "Genesis chain state hash mismatch, state hash generated from Bitcoin RPC ({}) does not match value in config ({})",
                hex::encode(genesis_chain_state.to_hash()),
                hex::encode(self.protocol_paramset().genesis_chain_state_hash)
            ));
        }

        if self.protocol_paramset().start_height < self.protocol_paramset().genesis_height {
            reasons.push(format!(
                "Start height is less than genesis height: {} < {}",
                self.protocol_paramset().start_height,
                self.protocol_paramset().genesis_height
            ));
        }

        if self.protocol_paramset().finality_depth < 1 {
            reasons.push(format!(
                "Finality depth ({}) cannot be less than 1",
                self.protocol_paramset().finality_depth
            ));
        }

        if !reasons.is_empty() {
            return Err(BridgeError::ConfigError(format!(
                "Invalid configuration due to: {}",
                reasons.join(" - ")
            )));
        }

        Ok(())
    }
```

**File:** core/src/main.rs (L59-62)
```rust
    config
        .check_general_requirements()
        .await
        .expect("Configuration is invalid");
```

**File:** core/src/verifier.rs (L688-696)
```rust
        if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount {
            let reason = format!(
                "Deposit amount is not correct, expected {}, got {}",
                self.config.protocol_paramset().bridge_amount,
                deposit_txout_in_chain.value
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
```

**File:** core/src/builder/sighash.rs (L50-55)
```rust
    pub fn get_num_required_nofn_sigs(&self, deposit_data: &DepositData) -> usize {
        deposit_data.get_num_operators()
            * self.protocol_paramset().num_round_txs
            * self.protocol_paramset().num_signed_kickoffs
            * self.get_num_required_nofn_sigs_per_kickoff(deposit_data)
    }
```

**File:** core/src/builder/transaction/operator_collateral.rs (L102-111)
```rust
    let total_required = (paramset.kickoff_amount + paramset.default_utxo_amount())
        .checked_mul(paramset.num_kickoffs_per_round as u64)
        .and_then(|kickoff_total| kickoff_total.checked_add(paramset.anchor_amount()))
        .ok_or_else(|| {
            BridgeError::ArithmeticOverflow("Total required amount calculation overflow")
        })?;

    let remaining_amount = input_amount.checked_sub(total_required).ok_or_else(|| {
        BridgeError::InsufficientFunds("Input amount insufficient for required outputs")
    })?;
```
