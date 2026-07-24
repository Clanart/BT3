### Title
Unauthenticated tx-sender JSON-RPC `send_tx` Endpoint Allows External Actors to Inject Arbitrary Transactions into the Bridge Sending Queue — (`File: crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The standalone `clementine-tx-sender` binary exposes a JSON-RPC server with a `send_tx` method that has **zero authentication**. When the server is bound to `0.0.0.0` (an explicitly supported configuration), any network-reachable actor can insert arbitrary Bitcoin transactions into the tx-sender queue with full control over `fee_paying_type`, `cancel_outpoints`, `cancel_txids`, `activate_txids`, and `activate_outpoints`. This is the direct analog of the PythPriceFeedUpdate bug: a privileged internal channel (the bridge actor → tx-sender path) is exposed as an unauthenticated external endpoint.

---

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds a plain `jsonrpsee` HTTP server with no TLS, no API key, and no IP allowlist:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await ...;
``` [1](#0-0) 

The `send_tx` handler accepts any caller's `InsertTryToSendParams` — including `fee_paying_type: CPFP` or `RBF` — and calls `TxSenderClient::insert_try_to_send` directly: [2](#0-1) 

The configuration layer explicitly permits binding to `0.0.0.0`:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(...)
}
``` [3](#0-2) 

The default in `run.sh` is `127.0.0.1`, but the code makes `0.0.0.0` a first-class supported value with no compensating auth layer.

By contrast, every gRPC endpoint in `clementine-core` is protected by mTLS with the `OnlyAggregatorAndSelf` interceptor that verifies the leaf certificate against the aggregator or self certificate: [4](#0-3) 

The tx-sender JSON-RPC server has no equivalent guard.

---

### Impact Explanation

**Fee-payer UTXO drain → liveness failure of safety-critical bridge transactions.**

The tx-sender maintains a pool of fee-payer UTXOs (`tx_sender_fee_payer_utxos`) used to CPFP-bump bridge transactions. An attacker who can reach the JSON-RPC port submits a flood of transactions with `fee_paying_type: CPFP`. The tx-sender's loop (`try_to_send_unconfirmed_txs`) processes every queued entry and attempts to attach fee-payer UTXOs to each one. Exhausting this pool prevents the tx-sender from fee-bumping legitimate bridge transactions: [5](#0-4) 

The transactions that depend on CPFP fee-bumping include `WatchtowerChallenge`, `Kickoff`, `Payout`, and `OperatorChallengeAck`: [6](#0-5) 

If `WatchtowerChallenge` transactions cannot be fee-bumped and fail to confirm within the challenge window, a malicious operator's false assertion goes unchallenged. The operator can then claim the reimbursement output, constituting theft of bridged BTC collateral. This satisfies the "slashable exposure of operator collateral" and "unauthorized state transition in challenge flow" impact criteria.

Additionally, the attacker can submit transactions with `cancel_outpoints` referencing bridge-controlled UTXOs. When those UTXOs are spent on-chain (by legitimate bridge activity), the attacker's queued entries are marked cancelled — but the reverse is also true: the attacker's entries consume DB rows and RPC polling cycles for every tx-sender loop iteration, degrading throughput for legitimate entries. [7](#0-6) 

---

### Likelihood Explanation

The standalone `clementine-tx-sender` binary is a production component with its own `main.rs`, database migrations, and a `run.sh` smoke-test script. It is designed to be deployed as a separate service: [8](#0-7) 

An operator deploying it in a containerized or cloud environment and setting `TX_SENDER_JSONRPC_BIND=0.0.0.0` (e.g., to allow the `clementine-core` process to reach it over a container network) exposes the endpoint to any peer on that network segment. No attacker capability beyond network reachability is required — no keys, no certificates, no prior state.

The `clementine-core` integration sets `jsonrpc: None` by default: [9](#0-8) 

So the vulnerability is scoped to the standalone deployment path, but that path is explicitly supported and documented.

---

### Recommendation

1. **Add authentication to the JSON-RPC server.** At minimum, require a shared secret (Bearer token or HMAC) in the HTTP `Authorization` header. Preferably, add mTLS using the same CA infrastructure already in place for gRPC.

2. **Restrict the bind address.** Remove `0.0.0.0` as a valid value for `TX_SENDER_JSONRPC_BIND`, or gate it behind an explicit `--allow-external` flag with a prominent warning.

3. **Add an IP allowlist.** Accept connections only from the configured `clementine-core` host(s).

4. **Rate-limit and cap queue depth.** Reject `insert_try_to_send` calls once the queue exceeds a configurable maximum, preventing DB bloat regardless of auth status.

---

### Proof of Concept

With the standalone tx-sender running (`TX_SENDER_JSONRPC_BIND=0.0.0.0`, `TX_SENDER_JSONRPC_PORT=3030`):

```python
import requests, json

# Craft a minimal valid Bitcoin transaction (version 2, one input, one output)
# with fee_paying_type=CPFP to force the tx-sender to allocate a fee-payer UTXO.
raw_tx_hex = "02000000" + "01" + "00"*32 + "00000000" + "ffffffff" + "01" + \
             "e803000000000000" + "00" + "00000000"  # simplified placeholder

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "send_tx",
    "params": [{
        "tx_metadata": None,
        "signed_tx_hex": raw_tx_hex,
        "fee_paying_type": "CPFP",
        "rbf_signing_info": None,
        "cancel_outpoints": [],
        "cancel_txids": [],
        "activate_txids": [],
        "activate_outpoints": [],
    }]
}

# No authentication required — any caller succeeds
resp = requests.post("http://<tx-sender-host>:3030", json=payload)
print(resp.json())  # {"jsonrpc":"2.0","id":1,"result":<try_to_send_id>}
```

Repeat in a loop with distinct transactions (different txids) to exhaust fee-payer UTXOs. The tx-sender's loop will attempt to CPFP-bump each entry, consuming the fee wallet. Legitimate `WatchtowerChallenge` and `Kickoff` transactions queued by the operator will stall without fee-payer UTXOs, missing their confirmation windows. [10](#0-9) [11](#0-10)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-111)
```rust
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

**File:** crates/clementine-tx-sender/src/config.rs (L197-212)
```rust
        #[cfg(feature = "json-rpc")]
        let jsonrpc = {
            let port = env_parse_optional::<u16>("TX_SENDER_JSONRPC_PORT")?;
            port.map(|port| {
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
            })
            .transpose()?
        };
```

**File:** core/src/rpc/interceptors.rs (L36-77)
```rust
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
}
```

**File:** crates/clementine-tx-sender/src/task.rs (L43-49)
```rust
        self.inner
            .try_to_send_unconfirmed_txs(
                fee_rate,
                self.current_tip_height,
                self.last_processed_tip_height != self.current_tip_height,
            )
            .await?;
```

**File:** core/src/tx_sender_queue.rs (L57-104)
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
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
```

**File:** crates/clementine-tx-sender/src/db/tx_sender.rs (L388-467)
```rust
    /// Returns unconfirmed try-to-send transactions that satisfy all queue conditions.
    ///
    /// A transaction is sendable when:
    /// - all activation dependencies have been seen and their relative block timelocks passed;
    /// - zero-timelock txid activations are either seen on-chain or currently in mempool;
    /// - no cancellation dependency has been seen;
    /// - the transaction itself has not been seen on-chain;
    /// - its previous effective fee rate is lower than `fee_rate`, or it has never been sent.
    ///
    /// Passing a very high `fee_rate` is used by callers to retrieve all active transactions
    /// after a new block, even when the market fee did not increase.
    ///
    /// # Returns
    ///
    /// A vector of tx-sender database ids that are ready to send or bump.
    pub async fn get_sendable_txs(
        &self,
        tx: Option<TxSenderDbTx<'_>>,
        fee_rate: FeeRateKvb,
        current_tip_height: u32,
    ) -> Result<Vec<u32>, BridgeError> {
        let select_query = sqlx::query_as::<_, (i32,)>(
            "WITH
                non_active_txs AS (
                    SELECT DISTINCT
                        activate_txid.activated_id AS tx_id
                    FROM
                        tx_sender_activate_try_to_send_txids AS activate_txid
                    WHERE
                        (
                            activate_txid.timelock > 0
                            AND (
                                activate_txid.seen_at_height IS NULL
                                OR (activate_txid.seen_at_height::bigint + activate_txid.timelock > $2::bigint)
                            )
                        )
                        OR (
                            activate_txid.timelock = 0
                            AND activate_txid.seen_at_height IS NULL
                            AND activate_txid.in_mempool IS NOT TRUE
                        )

                    UNION

                    SELECT DISTINCT
                        activate_outpoint.activated_id AS tx_id
                    FROM
                        tx_sender_activate_try_to_send_outpoints AS activate_outpoint
                    WHERE
                        activate_outpoint.seen_at_height IS NULL
                        OR (activate_outpoint.seen_at_height::bigint + activate_outpoint.timelock > $2::bigint)
                ),

                cancelled_txs AS (
                    SELECT DISTINCT
                        cancelled_id AS tx_id
                    FROM
                        tx_sender_cancel_try_to_send_outpoints
                    WHERE
                        seen_at_height IS NOT NULL

                    UNION

                    SELECT DISTINCT
                        cancelled_id AS tx_id
                    FROM
                        tx_sender_cancel_try_to_send_txids
                    WHERE
                        seen_at_height IS NOT NULL
                )

                SELECT
                    txs.id
                FROM
                    tx_sender_try_to_send_txs AS txs
                WHERE
                    txs.id NOT IN (SELECT tx_id FROM non_active_txs)
                    AND txs.id NOT IN (SELECT tx_id FROM cancelled_txs)
                    AND txs.seen_at_height IS NULL
                    AND (txs.effective_fee_rate IS NULL OR txs.effective_fee_rate < $1);",
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

**File:** core/src/config/mod.rs (L375-376)
```rust
            jsonrpc: None,
        }
```
