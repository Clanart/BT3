### Title
Aggregator gRPC service enforces no client-certificate authentication, allowing any network caller to invoke state-changing/broadcasting bridge operations - (File: core/src/servers.rs, core/src/rpc/interceptors.rs)

### Summary
The `AirdropZap`-style bug hint ("uninitialized/unrestricted entry point lets an unprivileged caller reach privileged state") maps in Clementine to the aggregator's gRPC server, which is explicitly documented and implemented to skip client-certificate authorization entirely, unlike the verifier/operator servers which enforce a `OnlyAggregatorAndSelf` mTLS check.

### Finding Description
Verifier and operator gRPC servers are wrapped with the `Interceptors::OnlyAggregatorAndSelf` interceptor, which inspects the peer's leaf TLS certificate and only allows requests whose certificate matches the configured aggregator or self certificate [1](#0-0) , with internal (`Internal*`) methods further restricted to only the entity's own certificate [2](#0-1) .

However, `create_aggregator_grpc_server` builds the aggregator's TLS/interceptor configuration through the same shared `create_grpc_server` helper, and the project's own documentation confirms: "The aggregator does not enforce client certificates but does use TLS for encryption." [3](#0-2)  This is corroborated in the server construction code, where `config.client_verification` gates whether the `OnlyAggregatorAndSelf` interceptor or the no-op `Noop` interceptor is installed [4](#0-3) , and the aggregator startup path only logs a warning if `client_verification` happens to be enabled, without itself enforcing per-caller identity binding beyond TLS transport encryption [5](#0-4) .

As a consequence, any network entity that can reach the aggregator's TCP port can invoke every RPC exposed by `ClementineAggregator`, including state-changing/broadcasting operations such as `Setup` (which collects and redistributes verifier keys and operator configs across the whole system) [6](#0-5)  and `NewDeposit` (which drives the full deposit nonce-gen/sign/finalize pipeline and produces the move-to-vault transaction) [7](#0-6) , as well as `OptimisticPayout`/withdrawal-broadcasting calls implemented in `AggregatorServer::optimistic_payout` [8](#0-7) . This exactly matches the report's underlying bug class ("a caller reaching a signing or state-changing method versus the party it is meant for") because the aggregator's privileged, orchestration-only surface — meant to be reachable solely by the trusted `clementine-backend` service — has no cryptographic caller-identity check at the gRPC layer.

### Impact Explanation
This matches the High-severity category "an unauthenticated state-changing or broadcasting call." An unauthenticated party could invoke `Setup` to trigger re-collection/redistribution of verifier keys and operator configs, or repeatedly invoke `NewDeposit`/`OptimisticPayout` to drive the multi-party signing ceremonies (nonce generation, partial-sig collection, finalize) outside of the intended backend-controlled flow, consuming verifier/operator resources and interfering with legitimate deposit/withdrawal processing. It does not, on its own, appear to allow direct theft of custodied BTC (values are still checked against on-chain/Citrea state deeper in the call stack), but it removes the intended access-control boundary at the aggregator, which the codebase's own documentation and interceptor design treat as a real security boundary for every other entity.

### Likelihood Explanation
High: this behavior is not a misconfiguration by a deployer — it is the aggregator's designed behavior ("aggregator does not enforce client certificates"), so it applies to every default/documented deployment. No credential, secret, or role is required by the attacker; only network reachability to the aggregator's gRPC port.

### Recommendation
Apply the same `OnlyAggregatorAndSelf`-style (or an aggregator-appropriate equivalent, e.g., backend-service certificate pinning) authentication/authorization interceptor to the aggregator's gRPC service instead of relying on TLS encryption alone, so that only the legitimate `clementine-backend` caller can invoke `Setup`, `NewDeposit`, `OptimisticPayout`, and other state-changing aggregator RPCs.

### Proof of Concept
Not independently executed (index-based analysis only; a background Devin session with terminal access would be needed to stand up the aggregator server and confirm empirically). Based on code inspection: an attacker with network access to the aggregator's exposed TCP port can establish a TLS connection (no client certificate required when `client_verification` is false, or with an arbitrary client certificate since it is never checked against `Noop`), then directly invoke `ClementineAggregator/Setup` or `ClementineAggregator/NewDeposit` via gRPC, both of which reach privileged application logic without any peer-certificate validation, unlike the equivalent operator/verifier RPC paths.

### Citations

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

**File:** core/src/rpc/interceptors.rs (L62-69)
```rust
    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
```

**File:** docs/usage.md (L192-203)
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

**File:** core/src/rpc/clementine.proto (L323-353)
```text
  repeated EntityDataWithId entities_compatibility_data = 1;
}

message EntityStatuses { repeated EntityStatusWithId entity_statuses = 1; }

// An operator is responsible for paying withdrawals. It has an unique ID and
// chain of UTXOs named `round_txs`. An operator also runs a verifier. These are
// connected to the same database and both have access to watchtowers'
// winternitz pubkeys.
service ClementineOperator {
  // Returns the operator's xonly public key
  //
  // Used by aggregator inside setup
  rpc GetXOnlyPublicKey(Empty) returns (XOnlyPublicKeyRpc) {}

  // Returns the protocol params that can affect the transactions in the
  // contract, syncing with citrea and version number for checking compatibility
  rpc GetCompatibilityParams(Empty) returns (CompatibilityParamsRPC) {}

  // Returns an operator's parameters. It will be called once, by the
  // aggregator, to set all the public keys.
  //
  // # Returns
  //
  // Returns an [`OperatorParams`], which includes operator's configuration and
  // Watchtower parameters.
  //
  // Used by aggregator inside setup
  rpc GetParams(Empty) returns (stream OperatorParams) {}

  // Returns an operator's deposit keys.
```

**File:** core/src/rpc/clementine.proto (L355-384)
```text
  // hashes.
  //
  // Used by aggregator inside new_deposit
  rpc GetDepositKeys(DepositParams) returns (OperatorKeys) {}

  // Returns the current status of tasks running on the operator and their last
  // synced heights.
  rpc GetCurrentStatus(Empty) returns (EntityStatus) {}

  // Sends the given outpoints to the operator's btc wallet.
  // The transaction will also be broadcasted to the network.
  // Each outpoint must pay to the operator's taproot address (xonly key, no
  // merkle root). The rpc also checks if any outpoint is the collateral of the
  // operator, and rejects the request if so. # Parameters
  // - outpoints: The outpoints to send to the operator's btc wallet
  // # Returns
  // - Raw signed tx that transfers the given outpoints to the operator's btc
  // wallet address
  rpc TransferToBtcWallet(Outpoints) returns (RawSignedTx) {}

  // Signs everything that includes Operator's burn connector.
  //
  // # Parameters
  //
  // - User's deposit information
  // - Nonce metadata
  //
  // # Returns
  //
  // - Operator burn Schnorr signature
```

**File:** core/src/rpc/aggregator.rs (L1009-1043)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn optimistic_payout(
        &self,
        request: tonic::Request<super::OptimisticWithdrawParams>,
    ) -> std::result::Result<tonic::Response<super::RawSignedTx>, tonic::Status> {
        tracing::info!("Optimistic payout rpc called");
        let opt_withdraw_params = request.into_inner();

        let withdraw_params =
            opt_withdraw_params
                .withdrawal
                .clone()
                .ok_or(Status::invalid_argument(
                    "Withdrawal params not found for optimistic payout",
                ))?;
        let (deposit_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(withdraw_params)?;
        tracing::info!("Parsed optimistic payout rpc params, deposit id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}, verification signature: {:?}", deposit_id, input_signature, input_outpoint, output_script_pubkey, output_amount, opt_withdraw_params.verification_signature);

        // check compatibility with verifiers only
        self.check_compatibility_with_actors(CompatibilityCheckScope::VerifiersOnly)
            .await?;

        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self
            .rpc
            .is_utxo_spent(&input_outpoint)
            .await
            .map_to_status()?
        {
            return Err(Status::invalid_argument(format!(
                "Withdrawal utxo is already spent: {input_outpoint:?}",
            )));
        }

```
