### Title
Unauthenticated TX-Sender JSON-RPC Server Allows Arbitrary Transaction Injection and Fee-Payer Wallet Drain — (File: `crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The standalone `clementine-tx-sender` JSON-RPC server exposes a `send_tx` endpoint with **no authentication** of any kind. When the service is configured with `TX_SENDER_JSONRPC_BIND=0.0.0.0`, any network-reachable attacker can submit arbitrary signed transactions with `FeePayingType::CPFP`, causing the tx-sender to fund each one from its own fee-payer UTXO wallet. Repeated calls drain the fee-payer wallet, permanently degrading bridge liveness and preventing the bridge from paying fees for critical protocol transactions (kickoffs, challenges, payouts, watchtower challenges).

---

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` constructs a plain `jsonrpsee` HTTP server with no authentication layer:

```rust
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        ...
    module.register_async_method("send_tx", |params, client, _| async move {
        let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;
        ...
        client.insert_try_to_send(..., req.fee_paying_type, ...).await
    })
``` [1](#0-0) 

The bind address is controlled by `TX_SENDER_JSONRPC_BIND`, which the config explicitly allows to be `0.0.0.0`:

```rust
let bind = env_optional("TX_SENDER_JSONRPC_BIND")
    .unwrap_or_else(|| "127.0.0.1".to_string());
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(...)
}
``` [2](#0-1) 

`InsertTryToSendParams` exposes the full `fee_paying_type` field to the caller: [3](#0-2) 

`insert_try_to_send` performs **no validation** of the submitted transaction's content or origin — it stores it directly in the DB queue: [4](#0-3) 

When `FeePayingType::CPFP` is specified, the tx-sender loop creates a CPFP child transaction funded from its own fee-payer UTXOs and broadcasts both. The fee-payer UTXOs are the tx-sender's own Bitcoin wallet balance, used to pay fees for all bridge protocol transactions. [5](#0-4) 

---

### Impact Explanation

An attacker who can reach the JSON-RPC endpoint (when `TX_SENDER_JSONRPC_BIND=0.0.0.0`) can:

1. **Drain the fee-payer wallet**: Submit many valid Bitcoin transactions (spending attacker-controlled UTXOs) with `FeePayingType::CPFP`. The tx-sender funds each with a CPFP child from its own wallet. Each CPFP child is a valid, broadcastable transaction that confirms on-chain, permanently removing funds from the fee-payer wallet.

2. **Bridge liveness failure**: Once the fee-payer wallet is exhausted, the bridge cannot pay fees for kickoff, challenge, payout, watchtower challenge, or reimbursement transactions. Operators unable to broadcast challenge responses within the protocol timelock windows lose their collateral and bridged BTC becomes unrecoverable.

The tx-sender-managed fee-payer balances are explicitly within the allowed impact scope.

---

### Likelihood Explanation

- The `json-rpc` feature is a documented, production-supported deployment mode with its own `main.rs` binary and `run.sh` smoke-test script.
- The config explicitly permits `0.0.0.0` binding with no warning or authentication requirement.
- No firewall or network-level restriction is enforced by the code itself.
- The attack requires only HTTP access to the JSON-RPC port and the ability to construct valid Bitcoin transactions (spending attacker-owned UTXOs), which is trivially achievable by any Bitcoin user. [6](#0-5) 

---

### Recommendation

Add authentication to the JSON-RPC server before it can be used in production with `0.0.0.0` binding. Options include:

1. **Bearer token / shared secret**: Require a configurable secret in the `Authorization` header; reject requests without it.
2. **Restrict to localhost only**: Remove `0.0.0.0` as a valid bind option, forcing the service to be reachable only via localhost or a Unix socket.
3. **mTLS on the JSON-RPC server**: Mirror the gRPC mTLS pattern already used for verifier/operator servers.

At minimum, add a hard warning or startup error when `TX_SENDER_JSONRPC_BIND=0.0.0.0` is set without an authentication secret configured.

---

### Proof of Concept

```bash
# Attacker creates a valid tx spending their own UTXO (dust output)
# and submits it to the tx-sender JSON-RPC with CPFP fee type.
# The tx-sender funds a CPFP child from its fee-payer wallet.

curl -sS -H 'content-type: application/json' \
  --data '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "send_tx",
    "params": [{
      "tx_metadata": null,
      "signed_tx_hex": "<attacker_signed_tx_hex>",
      "fee_paying_type": "CPFP",
      "rbf_signing_info": null,
      "cancel_outpoints": [],
      "cancel_txids": [],
      "activate_txids": [],
      "activate_outpoints": []
    }]
  }' \
  "http://<tx-sender-host>:<TX_SENDER_JSONRPC_PORT>"
```

Repeat in a loop. Each iteration causes the tx-sender to spend one fee-payer UTXO on a CPFP child. After enough iterations the fee-payer wallet is empty and the bridge can no longer broadcast any protocol transaction. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-89)
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
```

**File:** crates/clementine-tx-sender/src/config.rs (L30-35)
```rust
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
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

**File:** crates/tx-sender-types/src/clementine.rs (L158-170)
```rust
/// Parameters for inserting a transaction into the tx-sender queue.
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

**File:** crates/clementine-tx-sender/src/client.rs (L59-71)
```rust
    pub async fn insert_try_to_send(
        &self,
        dbtx: &mut crate::TxSenderTransaction,
        tx_metadata: Option<TxMetadata>,
        signed_tx: &Transaction,
        fee_paying_type: FeePayingType,
        rbf_signing_info: Option<RbfSigningInfo>,
        cancel_outpoints: &[OutPoint],
        cancel_txids: &[Txid],
        activate_txids: &[ActivatedWithTxid],
        activate_outpoints: &[ActivatedWithOutpoint],
    ) -> Result<u32, BridgeError> {
        let txid = signed_tx.compute_txid();
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
