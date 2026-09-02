No vulnerability found for this question.

`try_parse_from` in `core/src/config/mod.rs` is a static TOML config-file parser invoked only once at process startup (via `try_parse_file`/`get_config_from_args` in `core/src/cli.rs`) to load the node operator's own local configuration file — it never receives attacker-supplied data and is not reachable from any deposit, withdrawal, or gRPC request path. [1](#0-0) [2](#0-1) 

Address-network validation for attacker-supplied strings arriving over gRPC (e.g. `wallet_reimburse_address`, `recovery_taproot_address`) happens through separate code paths that call `require_network(...protocol_paramset().network)` explicitly, which is a distinct function from `try_parse_from` and outside the scope of this question's target. [3](#0-2) 

The claimed entrypoint ("a deposit or withdrawal request carrying a foreign-network address -> `try_parse_from`") does not exist in this codebase: no request handler calls `try_parse_from`, so the binding the question asserts cannot be traced or broken through this function.

### Citations

**File:** core/src/config/mod.rs (L227-234)
```rust
    /// Try to parse a `BridgeConfig` from given TOML formatted string and
    /// generate a `BridgeConfig`.
    pub fn try_parse_from(input: String) -> Result<Self, BridgeError> {
        match toml::from_str::<BridgeConfig>(&input) {
            Ok(c) => Ok(c),
            Err(e) => Err(BridgeError::ConfigError(e.to_string())),
        }
    }
```

**File:** core/src/cli.rs (L211-223)
```rust
fn get_config_from_args(args: Args) -> Result<BridgeConfig, BridgeError> {
    let config_source = get_config_source("READ_CONFIG_FROM_ENV", args.config.clone());

    let mut config =
        match config_source.wrap_err("Failed to determine source for configuration.")? {
            ConfigSource::File(config_file) => {
                // Read from configuration file ONLY
                BridgeConfig::try_parse_file(config_file)
                    .wrap_err("Failed to read configuration from file.")?
            }
            ConfigSource::Env => BridgeConfig::from_env()
                .wrap_err("Failed to read configuration from environment variables.")?,
        };
```

**File:** core/src/rpc/verifier.rs (L204-216)
```rust
        // check if address is valid
        let wallet_reimburse_address_checked = wallet_reimburse_address
            .clone()
            .require_network(self.verifier.config.protocol_paramset().network)
            .map_err(|e| {
                Status::invalid_argument(format!(
                    "Invalid operator reimbursement address: {:?} for bitcoin network {:?} for operator {:?}. ParseError: {}",
                    wallet_reimburse_address,
                    self.verifier.config.protocol_paramset().network,
                    operator_xonly_pk,
                    e
                ))
            })?;
```
