### Title
Secret Key Material Exposed via `Debug` Formatting in `BridgeConfig` and `TxSenderConfig` — (`core/src/config/env.rs`, `core/src/main.rs`, `crates/clementine-tx-sender/src/config.rs`)

### Summary

`BridgeConfig` derives `Debug` and stores `secret_key: SecretKey` as a plain, unguarded field. The startup path unconditionally calls `tracing::debug!("BridgeConfig from env: {:?}", config)`, which serialises the full struct — including the raw private key — into the log stream. A second, unconditional `println!("Configuration: {config:#?}")` in the `TestActor` command writes the same material to stdout. `TxSenderConfig` carries the same flaw: it derives `Debug` and holds both `secret_key: SecretKey` and `private_da_key: Option<SecretKey>` without redaction.

### Finding Description

`BridgeConfig` is declared with `#[derive(Debug, Clone, Deserialize)]` and contains `pub secret_key: SecretKey` as a plain field. [1](#0-0) 

The `secp256k1` crate's `SecretKey::fmt` implementation calls `self.display_secret()`, which emits the full 32-byte hex key. The codebase itself confirms this behaviour — the test suite calls `config.secret_key.display_secret().to_string()` to round-trip the value. [2](#0-1) 

At the end of `BridgeConfig::from_env()`, the entire struct is passed to `tracing::debug!` with the `{:?}` formatter: [3](#0-2) 

In `main.rs`, the `TestActor` command unconditionally prints the full config to stdout: [4](#0-3) 

`TxSenderConfig` repeats the pattern — `#[derive(Clone, Debug)]` over a struct that holds both the Taproot signing key and the optional DA signing key in plain `SecretKey` fields: [5](#0-4) 

### Impact Explanation

`BridgeConfig::secret_key` is the root key for every bridge actor. For an **operator** it controls the collateral UTXO chain, the reimbursement address, and all kickoff/payout transactions managed by `TxSender`. For a **verifier** it is the keypair used in every MuSig2 partial-sign call during deposit finalisation. Exposure of this key allows an attacker to:

- Drain the operator's collateral UTXOs and fee-payer UTXOs managed by `TxSender`.
- Produce valid operator signatures on arbitrary kickoff or payout transactions, redirecting reimbursement outputs.
- Produce valid verifier partial signatures, enabling deposit fraud or blocking honest challenge resolution.

### Likelihood Explanation

The `tracing::debug!` path fires on every startup when the operator or verifier binary is launched with any verbose flag (`-v`), or when an external log aggregator is configured at `DEBUG` level (a common troubleshooting step). The `TestActor` path fires unconditionally whenever that subcommand is invoked. Both paths are reachable without any special attacker capability — only the ability to read the process's log output or stdout, which is routinely captured by systemd journals, Docker log drivers, or centralised log collectors.

### Recommendation

1. **Wrap `secret_key` in `secrecy::Secret<SecretKey>`** (or implement a custom `Debug` that emits only the public key or a fixed placeholder) for both `BridgeConfig` and `TxSenderConfig`. The codebase already uses `secrecy::SecretString` for passwords — apply the same discipline to key material.
2. **Remove or redact the `tracing::debug!` call** in `BridgeConfig::from_env()`, or replace `{:?}` with a hand-written display that omits the secret key.
3. **Remove or gate the `println!("Configuration: {config:#?}")` call** in `main.rs` behind a compile-time `#[cfg(debug_assertions)]` guard, or replace it with a redacted summary.
4. **Audit `TxSenderConfig`** for any debug-print sites and apply the same redaction.

### Proof of Concept

```
# 1. Start the verifier with verbose logging enabled
RUST_LOG=debug ./clementine-core verifier --config bridge_config.toml -v

# 2. The startup log will contain a line such as:
# DEBUG clementine_core::config::env: BridgeConfig from env:
#   BridgeConfig { secret_key: SecretKey(#<32-byte-hex>), ... }

# 3. Alternatively, run the TestActor diagnostic command:
./clementine-core test-actor --config bridge_config.toml
# stdout will contain:
# Configuration: BridgeConfig { secret_key: SecretKey(#<32-byte-hex>), ... }

# 4. Use the extracted hex as the operator/verifier signing key to sign
#    arbitrary Bitcoin transactions spending collateral or fee-payer UTXOs.
``` [3](#0-2) [6](#0-5) [5](#0-4)

### Citations

**File:** core/src/config/mod.rs (L43-57)
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct BridgeConfig {
    /// Protocol paramset
    ///
    /// Sourced from either a file or the environment, is set to REGTEST_PARAMSET in tests
    ///
    /// Skipped in deserialization and replaced by either file/environment source. See [`crate::cli::get_cli_config`]
    #[serde(skip)]
    pub protocol_paramset: &'static ProtocolParamset,
    /// Host of the operator or the verifier
    pub host: String,
    /// Port of the operator or the verifier
    pub port: u16,
    /// Secret key for the operator or the verifier.
    pub secret_key: SecretKey,
```

**File:** core/src/config/env.rs (L255-256)
```rust
        tracing::debug!("BridgeConfig from env: {:?}", config);
        Ok(config)
```

**File:** core/src/config/env.rs (L278-280)
```rust
            "SECRET_KEY",
            default_config.secret_key.display_secret().to_string(),
        );
```

**File:** core/src/main.rs (L136-140)
```rust
            let address = Actor::new(config.secret_key, config.protocol_paramset.network).address;

            println!("Configuration: {config:#?}");
            println!("Bitcoin address: {address}");
            println!("Bitcoin node addresses: {addresses:?}");
```

**File:** crates/clementine-tx-sender/src/config.rs (L38-49)
```rust
#[derive(Clone, Debug)]
pub struct TxSenderConfig {
    pub network: Network,
    /// Taproot signing key used by tx-sender.
    ///
    /// In clementine_core usage this is derived from `BridgeConfig.secret_key`.
    /// In standalone usage it is sourced from env `SECRET_KEY`.
    pub secret_key: SecretKey,
    /// Optional Citrea DA blob signing key.
    ///
    /// If not provided, tx-sender falls back to `secret_key` for Citrea blob signing.
    pub private_da_key: Option<SecretKey>,
```
