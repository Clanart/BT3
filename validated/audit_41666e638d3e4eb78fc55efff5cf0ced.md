### Title
gRPC Authentication Completely Bypassed When `client_verification = false` — (`core/src/servers.rs`, `core/src/rpc/interceptors.rs`)

### Summary

When `config.client_verification` is set to `false`, the Clementine verifier and operator gRPC servers install a `Noop` interceptor and omit the TLS `client_ca_root`, meaning no client certificate is required and no caller identity is checked. Any network-reachable party can invoke every gRPC method — including `deposit_sign`, `optimistic_payout_sign`, and `withdraw` — without presenting a certificate. This misconfiguration is explicitly allowed on every non-mainnet network (testnet4, signet, regtest) without any warning, and the in-code comment on the field falsely claims CA validation still occurs regardless of the setting.

### Finding Description

**Root cause — `core/src/servers.rs` lines 106–138**

`create_grpc_server` branches on `config.client_verification`:

```rust
let tls_config = if config.client_verification {
    ServerTlsConfig::new()
        .identity(server_identity)
        .client_ca_root(client_ca)   // mTLS: client cert required + CA-validated
} else {
    ServerTlsConfig::new().identity(server_identity)  // TLS only, no client cert
};

let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
    } else {
        Noop   // passes every request unconditionally
    },
);
```

When `client_verification = false`:
- The CA root is not added → the TLS layer does not request or validate any client certificate.
- The interceptor is `Noop` → `Interceptors::call` returns `Ok(req)` for every request with no identity check.

**Misleading comment — `core/src/config/mod.rs` line 134–138**

```rust
/// Whether client certificates should be restricted to Aggregator and Self certificates.
///
/// Client certificates are always validated against the CA certificate
/// according to mTLS regardless of this setting.
pub client_verification: bool,
```

The comment is incorrect: when `client_verification = false`, no CA root is configured and no client certificate is validated at all.

**Guard only covers mainnet — `core/src/config/mod.rs` lines 292–303**

```rust
pub fn check_mainnet_requirements(&self, actor_type: cli::Actor) -> Result<(), BridgeError> {
    if self.protocol_paramset().network != Network::Bitcoin {
        return Ok(());   // check skipped entirely on testnet4 / signet / regtest
    }
    if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
        && !self.client_verification
    {
        misconfigs.push("CLIENT_VERIFICATION=false".to_string());
    }
    ...
}
```

On any non-mainnet network the function returns `Ok(())` immediately, so a verifier or operator running on testnet4 or signet with `client_verification = false` starts successfully with no warning.

**Exploit path**

1. Operator or verifier is deployed on testnet4/signet with `client_verification = false` (e.g., for debugging, or because the misleading comment led the operator to believe CA validation still occurs).
2. Attacker connects to the gRPC TCP port — no certificate needed, TLS handshake succeeds as one-way TLS.
3. `Noop` interceptor passes the request; the handler executes.
4. Attacker calls `deposit_sign` on a verifier with a crafted `DepositSignSession`, obtaining partial MuSig2 signatures for an attacker-controlled deposit.
5. Alternatively, if `aggregator_verification_address` is also `None` (the other optional security flag), the attacker calls `withdraw` directly on the operator with a valid Citrea-registered withdrawal, bypassing the intended aggregator-only authorization path entirely.

### Impact Explanation

- **Authentication bypass**: The `OnlyAggregatorAndSelf` interceptor — the sole role-enforcement layer for verifier and operator gRPC — is completely removed. Any network peer becomes indistinguishable from the aggregator.
- **Privileged signing exposure**: `deposit_sign` and `optimistic_payout_sign` on verifiers, and `withdraw` / `internal_withdraw` on operators, are reachable without any credential.
- **Bridge fund risk**: An attacker with a valid Citrea withdrawal registration can call `withdraw` on the operator directly, triggering a payout transaction that moves bridge-controlled BTC to an attacker-chosen output, bypassing aggregator-level checks.
- **Collateral/nonce exhaustion**: Repeated `nonce_gen` calls exhaust the verifier's in-memory nonce session pool (`MAX_ALL_SESSIONS_BYTES` / `MAX_NUM_SESSIONS`), evicting legitimate sessions and stalling ongoing deposits.

### Likelihood Explanation

- `client_verification` defaults to `true` in `BridgeConfig::default()` and in the shipped TOML examples, but the env-var parser sets it to `false` if `CLIENT_VERIFICATION` is absent or any value other than `"true"` or `"1"`.
- The misleading comment ("always validated … regardless of this setting") actively encourages operators to believe the system remains secure when the flag is off.
- No runtime warning is emitted on non-mainnet when `client_verification = false`; the server starts silently.
- Testnet4 is a real deployment target with real bridge state.

### Recommendation

1. **Short term**: Emit a startup `tracing::error!` (or hard-abort) whenever `client_verification = false` on any network, not only mainnet. Remove or correct the misleading comment.
2. **Long term**: Rename the field to something that makes the insecurity explicit (e.g., `disable_client_verification_UNSAFE`) and require an additional `--allow-insecure` CLI flag to start with it disabled, mirroring the external report's recommendation.

### Proof of Concept

```
# Start verifier on testnet4 with client_verification = false
CLIENT_VERIFICATION=0 ./clementine-core verifier --config testnet4.toml
# => starts successfully, no warning

# Attacker connects (no client cert needed — one-way TLS only)
grpcurl -insecure <verifier_host>:<port> \
  clementine.ClementineVerifier/NonceGen \
  '{"num_nonces": 100}'
# => returns session_id + 100 public nonces, no authentication error

# Attacker calls deposit_sign with crafted deposit params
grpcurl -insecure <verifier_host>:<port> \
  clementine.ClementineVerifier/DepositSign \
  '<crafted DepositSignSession>'
# => verifier signs attacker-controlled deposit data
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/servers.rs (L106-139)
```rust
            let tls_config = if config.client_verification {
                ServerTlsConfig::new()
                    .identity(server_identity)
                    .client_ca_root(client_ca)
            } else {
                ServerTlsConfig::new().identity(server_identity)
            };

            let service = InterceptedService::new(
                service,
                if config.client_verification {
                    let client_cert = CertificateDer::from_pem_file(&config.client_cert_path)
                        .wrap_err(format!(
                            "Failed to read client certificate from {}",
                            config.client_cert_path.display()
                        ))?
                        .to_owned();

                    let aggregator_cert =
                        CertificateDer::from_pem_file(&config.aggregator_cert_path)
                            .wrap_err(format!(
                                "Failed to read aggregator certificate from {}",
                                config.aggregator_cert_path.display()
                            ))?
                            .to_owned();

                    OnlyAggregatorAndSelf {
                        aggregator_cert,
                        our_cert: client_cert,
                    }
                } else {
                    Noop
                },
            );
```

**File:** core/src/rpc/interceptors.rs (L22-33)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
}
```

**File:** core/src/config/mod.rs (L134-138)
```rust
    /// Whether client certificates should be restricted to Aggregator and Self certificates.
    ///
    /// Client certificates are always validated against the CA certificate
    /// according to mTLS regardless of this setting.
    pub client_verification: bool,
```

**File:** core/src/config/mod.rs (L291-303)
```rust
    /// Checks various variables if they are correct for mainnet deployment.
    pub fn check_mainnet_requirements(&self, actor_type: cli::Actor) -> Result<(), BridgeError> {
        if self.protocol_paramset().network != Network::Bitcoin {
            return Ok(());
        }

        let mut misconfigs = Vec::new();

        if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
            && !self.client_verification
        {
            misconfigs.push("CLIENT_VERIFICATION=false".to_string());
        }
```

**File:** core/src/config/env.rs (L181-182)
```rust
        let client_verification =
            read_string_from_env("CLIENT_VERIFICATION").is_ok_and(|s| s == "true" || s == "1");
```

**File:** core/src/rpc/operator.rs (L209-239)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
            let verification_signature = params
                .verification_signature
                .map(|sig| {
                    PrimitiveSignature::from_str(&sig).map_err(|e| {
                        Status::invalid_argument(format!("Invalid verification signature: {e}"))
                    })
                })
                .transpose()?;
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
                        withdrawal_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
            }
        }
```

**File:** core/src/main.rs (L75-95)
```rust
        Command::Verifier => {
            tracing::info!("Starting verifier server...");
            config
                .check_mainnet_requirements(cli::Actor::Verifier)
                .expect("Illegal configuration options!");

            create_verifier_grpc_server::<CitreaClient>(config.clone())
                .await
                .expect("Can't create verifier server")
                .1
        }
        Command::Operator => {
            tracing::info!("Starting operator server...");
            config
                .check_mainnet_requirements(cli::Actor::Operator)
                .expect("Illegal configuration options!");

            create_operator_grpc_server::<CitreaClient>(config.clone())
                .await
                .expect("Can't create operator server")
                .1
```
