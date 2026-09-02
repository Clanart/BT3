### Title
Unauthenticated access to the Aggregator's gRPC service allows any network caller to trigger fund-moving actions (`Withdraw`, `Setup`, `NewDeposit`) - (File: core/src/servers.rs)

### Summary
The Aggregator's gRPC server is started without any client-certificate/identity check, unlike the Verifier and Operator servers, which enforce that only the Aggregator's own certificate (or the entity's own certificate for `Internal*` methods) may invoke state-changing RPCs. Because the Aggregator holds the trusted client certificate that Verifiers/Operators accept, any unauthenticated caller able to reach the Aggregator's network endpoint can drive privileged, fund-moving operations end-to-end.

### Finding Description
`create_grpc_server` builds an `InterceptedService` and only installs the `OnlyAggregatorAndSelf` interceptor `if config.client_verification` is true — and even then, the resulting cert check restricts callers of Verifier/Operator servers to the party matching the `aggregator_cert_path` or the entity's own `client_cert_path` [1](#0-0) . However, `create_aggregator_grpc_server` builds `ClementineAggregatorServer` and calls `create_grpc_server` directly on it with no separate authorization layer for the Aggregator's own inbound service [2](#0-1) . This is explicitly documented: "The aggregator does not enforce client certificates but does use TLS for encryption" [3](#0-2) .

The `Interceptors::Noop` variant (used when `client_verification` is false, and effectively the only enforcement path relevant to the Aggregator's own inbound connections) performs no authentication at all [4](#0-3) .

Downstream, Verifier/Operator RPCs such as `Withdraw` trust the caller purely via mTLS identity (`leaf_cert == aggregator_cert`), and only optionally re-check an ECDSA "verification signature" against `aggregator_verification_address` — a value that is `Option<Address>` and skipped entirely when unset in config [5](#0-4) . Thus, the only binding between "the party meant to authorize a withdrawal" and "the party who actually can call it" is: (1) the Aggregator's mTLS identity when calling out to Operators/Verifiers, and (2) whoever can reach the Aggregator's own unauthenticated gRPC port.

This breaks the equality: `caller authorized to invoke Withdraw/Setup/NewDeposit on the Aggregator == holder of aggregator/administrative privilege`. Before the attack: only the legitimate aggregator operator can drive `Withdraw`. After the attack: any network peer reaching the Aggregator's port can invoke `Withdraw` (`core/src/rpc/aggregator.rs:1811-1887`) [6](#0-5) , `Setup` [7](#0-6) , and other privileged coordination RPCs, causing the Aggregator to use its own trusted client certificate to invoke fund-related operator/verifier actions on the attacker's behalf.

### Impact Explanation
This matches the High-impact category "an unauthenticated state-changing or broadcasting call": an external, unprivileged caller reaching the Aggregator's network endpoint can invoke RPCs (`Withdraw`, `Setup`, `NewDeposit`) that cause the Aggregator to broadcast withdrawal-related requests to Operators using its own trusted mTLS identity. If `aggregator_verification_address` is unset (which is optional/`Option`), there is no secondary signature check at the Operator, so the Aggregator's identity alone authorizes the payout preparation and broadcast — meaning reaching the Aggregator is functionally equivalent to reaching an admin-only broadcasting capability without any credential.

### Likelihood Explanation
Likelihood depends on network exposure of the Aggregator's gRPC port and whether `aggregator_verification_address` is configured. The code path and default behavior (Aggregator performs no client-cert enforcement, documented as intentional) makes this reachable by design whenever the Aggregator endpoint is exposed to any party other than its trusted operator; the only mitigating control (`aggregator_verification_address`) is optional configuration, not an enforced protocol invariant.

### Recommendation
Add authentication/authorization to the Aggregator's own gRPC ingress (e.g., mTLS client-cert allow-listing analogous to `OnlyAggregatorAndSelf`, or require the ECDSA verification signature unconditionally rather than only when `aggregator_verification_address` is set), so that reaching the Aggregator's network endpoint requires proof of being an authorized administrator, not just network reachability.

### Proof of Concept
1. Deploy Aggregator with default/no client-cert restriction (as built by `create_aggregator_grpc_server`, which never applies `OnlyAggregatorAndSelf` to its own service) and leave `aggregator_verification_address` unset.
2. From an arbitrary network client (no valid certificate required beyond generic TLS), connect to the Aggregator's gRPC port and call `Withdraw` (`ClementineAggregator::withdraw` in `core/src/rpc/aggregator.rs:1811`) with attacker-chosen withdrawal parameters.
3. The Aggregator, using its own trusted client certificate, forwards the request to Operator(s) via `operator.withdraw(request)` [8](#0-7) ; the Operator's `Withdraw` RPC accepts the call because the caller (Aggregator) presents `aggregator_cert`, and since `aggregator_verification_address` is unset, no additional signature is required [9](#0-8) .
4. The attacker has thus triggered a privileged, fund-related broadcasting action without holding any administrative credential.

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

**File:** core/src/servers.rs (L293-317)
```rust
pub async fn create_aggregator_grpc_server(
    config: BridgeConfig,
) -> Result<(std::net::SocketAddr, oneshot::Sender<()>), BridgeError> {
    let addr: std::net::SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .wrap_err("Failed to parse address")?;
    let aggregator_server = AggregatorServer::new(config.clone()).await?;
    aggregator_server.start_background_tasks().await?;

    let svc = ClementineAggregatorServer::new(aggregator_server)
        .max_encoding_message_size(config.grpc.max_message_size)
        .max_decoding_message_size(config.grpc.max_message_size);

    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }

    let (server_addr, shutdown_tx) =
        create_grpc_server(addr.into(), svc, "Aggregator", &config).await?;

    match server_addr {
        ServerAddr::Tcp(socket_addr) => Ok((socket_addr, shutdown_tx)),
        _ => Err(BridgeError::ConfigError("Expected TCP address".into())),
    }
}
```

**File:** docs/usage.md (L192-204)
```markdown
## RPC Authentication

Clementine uses mutual TLS (mTLS) to secure gRPC communications between entities
and to authenticate clients. Client certificates are verified and filtered by
the verifier/operator to ensure that:

1. Verifier/Operator methods can only be called by the aggregator (using
   aggregator's client certificate `aggregator_cert_path`)
2. Internal methods can only be called by the entity's own client certificate
   (using the entity's client certificate `client_cert_path`)

The aggregator does not enforce client certificates but does use TLS for encryption.

```

**File:** core/src/rpc/interceptors.rs (L4-32)
```rust
pub enum Interceptors {
    OnlyAggregatorAndSelf {
        aggregator_cert: CertificateDer<'static>,
        our_cert: CertificateDer<'static>,
    },
    Noop,
}

fn is_internal(req: &Request<()>) -> bool {
    // This normally doesn't exist but we add it in the AddMethodMiddleware
    let Some(path) = req.metadata().get("grpc-method") else {
        // No grpc method? this should not happen
        tracing::error!("Missing grpc-method header in request");
        return false;
    };
    path.as_bytes().starts_with(b"Internal")
}

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
```

**File:** core/src/rpc/operator.rs (L192-239)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn withdraw(
        &self,
        request: Request<WithdrawParamsWithSig>,
    ) -> Result<Response<RawSignedTx>, Status> {
        tracing::info!("Withdraw rpc called");
        let params = request.into_inner();
        let withdraw_params = params.withdrawal.ok_or(Status::invalid_argument(
            "Withdrawal params not found for withdrawal",
        ))?;
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(withdraw_params)?;

        tracing::warn!(
            "Parsed withdraw rpc params, withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}, verification signature: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount, params.verification_signature
        );

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

**File:** core/src/rpc/aggregator.rs (L1318-1321)
```rust
    ) -> Result<Response<VerifierPublicKeys>, Status> {
        tracing::info!("Setup rpc called");
        self.check_compatibility_with_actors(CompatibilityCheckScope::Both)
            .await?;
```

**File:** core/src/rpc/aggregator.rs (L1811-1827)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn withdraw(
        &self,
        request: Request<AggregatorWithdrawalInput>,
    ) -> Result<Response<AggregatorWithdrawResponse>, Status> {
        tracing::warn!("Withdraw rpc called");
        let request = request.into_inner();
        let (withdraw_params_with_sig, operator_xonly_pks) = (
            request.withdrawal.ok_or(Status::invalid_argument(
                "withdrawalParamsWithSig is missing",
            ))?,
            request.operator_xonly_pks,
        );
        // check compatibility with operators only
        self.check_compatibility_with_actors(CompatibilityCheckScope::OperatorsOnly)
            .await?;

```

**File:** core/src/rpc/aggregator.rs (L1870-1887)
```rust
        let operators = self
            .get_operator_clients()
            .iter()
            .zip(current_operator_xonly_pks.into_iter());
        let withdraw_futures = operators
            .filter(|(_, xonly_pk)| {
                // check if operator_xonly_pks is empty or contains the operator's xonly public key
                operator_xonly_pks_from_rpc.is_empty()
                    || operator_xonly_pks_from_rpc.contains(xonly_pk)
            })
            .map(|(operator, operator_xonly_pk)| {
                let mut operator = operator.clone();
                let params = withdraw_params_with_sig.clone();
                let mut request = Request::new(params);
                request.set_timeout(WITHDRAWAL_TIMEOUT);
                async move { (operator.withdraw(request).await, operator_xonly_pk) }
            });

```
