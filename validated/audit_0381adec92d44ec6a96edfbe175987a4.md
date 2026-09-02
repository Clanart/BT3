### Title
Aggregator gRPC server exposes state-changing/broadcasting RPCs to unauthenticated callers - (File: `core/src/servers.rs`, `core/src/rpc/aggregator.rs`)

### Summary
The Kinto report describes an `EntryPoint` that lets any anonymous account invoke privileged entry points (`handleOps`/`handleAggregatedOps`) with no access control. The direct analog in this repository is the Clementine aggregator's gRPC server: unlike the verifier and operator servers, it performs no client-certificate authentication, so any network caller with a TLS connection can invoke every aggregator RPC, including state-changing/broadcasting methods.

### Finding Description
Verifier and operator servers restrict every non-public method to callers presenting the aggregator's or their own client certificate via the `Interceptors::OnlyAggregatorAndSelf` interceptor [1](#0-0) . The aggregator server, however, is documented and implemented to skip this check entirely: `create_aggregator_grpc_server` builds its service without any per-method certificate filtering, and even warns that "Client verification is enabled" only refers to TLS transport, not caller authorization [2](#0-1) . This is explicitly confirmed in the docs: "The aggregator does not enforce client certificates but does use TLS for encryption" [3](#0-2) .

As a consequence, any caller that can open a TLS connection to the aggregator's port (with or without `client_verification` enabled, since it is never checked for this service) can invoke state-changing and broadcasting RPCs such as `InternalSendTx` [4](#0-3) , `SendMoveToVaultTx` [5](#0-4) , `Withdraw`, `OptimisticPayout`, and `NewDeposit` [6](#0-5) . Because the aggregator relays these calls to verifiers/operators using its own trusted client certificate, an unauthenticated caller effectively acts as a confused deputy, driving privileged N-of-N signing/broadcast workflows on the verifier/operator fleet through the one gRPC entry point that has no caller authentication.

### Impact Explanation
This matches the "unauthenticated state-changing or broadcasting call" High-severity class: `InternalSendTx` allows any anonymous caller to push an arbitrary raw transaction into the operator/aggregator's tx-sender queue for broadcast [7](#0-6) , and `SendMoveToVaultTx`/`Withdraw`/`NewDeposit`/`OptimisticPayout` let anonymous callers trigger the bridge's core signing/broadcasting pipelines on verifiers and operators without any proof of being the aggregator, the depositor, or Citrea. While several of these endpoints have secondary validation (structural checks on the move-tx, ECDSA `verification_signature` checks for withdrawals), the initial authorization boundary — "only the aggregator may call this" — is missing entirely at the transport layer, which is the boundary the certificate scheme is designed to enforce.

### Likelihood Explanation
The condition is not a misconfiguration; it is the current, documented, always-on behavior of `create_aggregator_grpc_server`, so it applies to every deployment following the documented setup, exactly like the Kinto finding ("may become a problem in the early stages of the network... consider this when deploying"). Any actor capable of reaching the aggregator's gRPC port over the network can exploit it without holding any privileged role, key, or certificate.

### Recommendation
Apply the same `OnlyAggregatorAndSelf`/allow-list style interceptor (or an equivalent authentication mechanism, e.g., mTLS with a defined set of permitted client identities, or API-key/service-mesh authentication) to the aggregator's gRPC service, distinguishing "public" read-only RPCs from state-changing/broadcasting ones (`InternalSendTx`, `SendMoveToVaultTx`, `NewDeposit`, `Withdraw`, `OptimisticPayout`, `Setup`, etc.), and reject unauthenticated callers for the latter category.

### Proof of Concept
1. Deploy the aggregator per documented configuration (`client_verification` true or false — irrelevant, since the aggregator ignores it) as shown in `create_aggregator_grpc_server` [2](#0-1) .
2. From any machine, open a TLS connection to the aggregator's gRPC port using any self-signed client certificate (no CA-issued aggregator/self certificate required, since the aggregator never inspects `peer_certs()`).
3. Call `InternalSendTx` with a syntactically valid raw transaction; the server accepts it and enqueues it for broadcast without checking the caller's identity [8](#0-7) , or call `SendMoveToVaultTx`/`NewDeposit`/`Withdraw` to drive verifier/operator signing flows that are otherwise restricted to the aggregator's certificate.

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

**File:** core/src/rpc/aggregator.rs (L1269-1312)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_send_tx(
        &self,
        request: Request<clementine::SendTxRequest>,
    ) -> Result<Response<Empty>, Status> {
        #[cfg(not(feature = "automation"))]
        {
            Err(Status::unimplemented("Automation is not enabled"))
        }
        #[cfg(feature = "automation")]
        {
            let send_tx_req = request.into_inner();
            let fee_type = send_tx_req.fee_type();
            let signed_tx: bitcoin::Transaction = send_tx_req
                .raw_tx
                .ok_or(Status::invalid_argument("Missing raw_tx"))?
                .try_into()?;
            tracing::warn!(
                "Internal send tx rpc called with feetype: {:?}, tx hex: {}",
                fee_type,
                bitcoin::consensus::encode::serialize_hex(&signed_tx)
            );

            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    None,
                    &signed_tx,
                    fee_type.try_into()?,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
            dbtx.commit()
                .await
                .map_err(|e| Status::internal(format!("Failed to commit db transaction: {e}")))?;
            Ok(Response::new(Empty {}))
        }
    }
```

**File:** core/src/rpc/aggregator.rs (L1973-2017)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn send_move_to_vault_tx(
        &self,
        request: Request<clementine::SendMoveTxRequest>,
    ) -> Result<Response<clementine::Txid>, Status> {
        tracing::info!("Send move to vault tx rpc called");
        #[cfg(not(feature = "automation"))]
        {
            let _ = request;
            return Err(Status::unimplemented(
                "Automation is disabled, cannot automatically send move to vault tx.",
            ));
        }

        #[cfg(feature = "automation")]
        {
            use bitcoin::Amount;
            use std::sync::Arc;

            use crate::builder::{
                address::create_taproot_address,
                script::{CheckSig, Multisig, SpendableScript},
                transaction::anchor_output,
            };

            let request = request.into_inner();
            let movetx: bitcoin::Transaction = bitcoin::consensus::deserialize(
                &request
                    .raw_tx
                    .ok_or_eyre("raw_tx is required")
                    .map_to_status()?
                    .raw_tx,
            )
            .wrap_err("Failed to deserialize movetx")
            .map_to_status()?;
            let deposit_outpoint: bitcoin::OutPoint = request
                .deposit_outpoint
                .ok_or(Status::invalid_argument("deposit_outpoint is required"))?
                .try_into()?;

            tracing::info!(
                "Parsed send move to vault tx rpc params, deposit outpoint: {:?}, movetx hex: {}",
                deposit_outpoint,
                bitcoin::consensus::encode::serialize_hex(&movetx)
            );
```

**File:** core/src/rpc/clementine.proto (L765-781)
```text
  rpc NewDeposit(Deposit) returns (RawSignedTx) {}

  // Call's withdraw on all operators
  // Used by the clementine-backend service to initiate a withdrawal
  // If the operator's xonly public keys list is empty, the withdrawal will be
  // sent to all operators. If not, only the operators in the list will be sent
  // the withdrawal request.
  rpc Withdraw(AggregatorWithdrawalInput) returns (AggregatorWithdrawResponse) {
  }

  // Perform an optimistic payout to reimburse a peg-out from Citrea
  rpc OptimisticPayout(OptimisticWithdrawParams) returns (RawSignedTx) {}

  // Send a pre-signed tx to the network
  rpc InternalSendTx(SendTxRequest) returns (Empty) {}

  rpc SendMoveToVaultTx(SendMoveTxRequest) returns (Txid) {}
```
