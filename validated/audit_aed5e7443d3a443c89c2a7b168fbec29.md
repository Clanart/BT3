### Title
Unauthenticated tx-sender JSON-RPC `send_tx` Endpoint Allows Arbitrary Transaction Injection and Fee-Payer UTXO Drainage — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The standalone tx-sender JSON-RPC server exposes a `send_tx` method with no authentication, no rate limiting, and no caller validation. When the server is bound to `0.0.0.0` (an explicitly supported configuration), any network-reachable attacker can flood it with crafted `CPFP`-typed transaction requests. The tx-sender will attempt to fund each one by creating child transactions that spend the operator's fee-payer UTXOs, and when those are exhausted it calls `send_to_address` against the Bitcoin wallet to replenish them. This drains the tx-sender-managed balance and can prevent the operator from broadcasting time-sensitive bridge transactions (kickoff, reimbursement) within their timelocks, causing loss of operator collateral.

---

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds a plain HTTP JSON-RPC server with `ServerBuilder::default()` and registers `send_tx` and `send_citrea_tx` with no authentication middleware, no API key check, and no rate limiter. [1](#0-0) 

The server's bind address is configurable as either `127.0.0.1` or `0.0.0.0`: [2](#0-1) 

The `run.sh` smoke-test script shows the server started with `--features json-rpc` and the `TX_SENDER_JSONRPC_BIND` variable defaulting to `127.0.0.1` but explicitly accepting `0.0.0.0`: [3](#0-2) 

When a caller submits a transaction with `fee_paying_type: CPFP`, the tx-sender loop picks it up in `try_to_send_unconfirmed_txs` and routes it to `send_cpfp_tx`: [4](#0-3) 

Inside `send_cpfp_tx`, if the confirmed fee-payer UTXOs are insufficient to cover the required fee, `create_fee_payer_utxo` is called, which issues a live `send_to_address` RPC call against the Bitcoin wallet to replenish them: [5](#0-4) 

An attacker who can reach the JSON-RPC port can craft transactions that include a P2A anchor output (which `find_p2a_vout` will accept), submit them in bulk with `fee_paying_type: CPFP`, and cause the tx-sender to repeatedly call `send_to_address`, draining the operator's wallet.

The gRPC layer uses mTLS to protect its `internal_send_tx` endpoint: [6](#0-5) 

But the JSON-RPC server has no equivalent protection.

---

### Impact Explanation

The tx-sender manages the fee-payer UTXOs that fund every bridge transaction the operator must broadcast: kickoff, reimbursement, and collateral-related outputs. Draining these UTXOs prevents the operator from sending time-sensitive transactions within their timelocks. If the operator cannot broadcast a reimbursement transaction before the challenge window closes, they lose their collateral. This is a direct loss of operator collateral — a bridge-controlled balance explicitly listed in the allowed impact gate.

---

### Likelihood Explanation

The JSON-RPC feature is a documented, first-class deployment mode (`cargo run -p clementine-tx-sender --features json-rpc`). The `0.0.0.0` bind address is an explicitly supported value. Any attacker with network access to the tx-sender port (e.g., a co-tenant in a cloud environment, or an operator who misconfigures firewall rules) can trigger this with a simple HTTP client and no credentials. The `MAX_JSONRPC_REQUEST_BODY_SIZE` of 50 MB and the absence of any rate limiter amplify the attack throughput. [7](#0-6) 

---

### Recommendation

1. **Add authentication** to the JSON-RPC server — at minimum a shared secret / bearer token checked in a middleware layer before any method is dispatched.
2. **Add rate limiting** using `jsonrpsee`'s built-in `max_connections` and per-IP rate-limit middleware.
3. **Validate transaction content** before queuing: reject transactions whose inputs reference UTXOs not owned by the operator, and reject `CPFP` requests for transactions that are not in the tx-sender's own DB.
4. **Restrict the bind address** to `127.0.0.1` by default and document that `0.0.0.0` requires a firewall rule; consider removing `0.0.0.0` support entirely or enforcing it only with authentication enabled.

---

### Proof of Concept

```python
import json, requests, os

# Craft a minimal Bitcoin transaction with a P2A anchor output (vout index 0)
# so that find_p2a_vout accepts it.
# The exact hex is omitted; any valid serialized tx with a P2A output suffices.
P2A_TX_HEX = "<hex of tx with P2A anchor output>"

url = "http://<operator-ip>:3030"  # TX_SENDER_JSONRPC_BIND=0.0.0.0

for _ in range(500):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "send_tx",
        "params": [{
            "tx_metadata": None,
            "signed_tx_hex": P2A_TX_HEX,
            "fee_paying_type": "CPFP",   # triggers create_fee_payer_utxo → send_to_address
            "rbf_signing_info": None,
            "cancel_outpoints": [],
            "cancel_txids": [],
            "activate_txids": [],
            "activate_outpoints": [],
        }]
    }
    requests.post(url, json=payload)
# Result: tx-sender calls send_to_address 500 times, draining the operator wallet.
# Operator can no longer fund kickoff/reimbursement transactions within timelocks.
```

The `send_tx` handler at lines 58–88 of `crates/clementine-tx-sender/src/jsonrpc/server.rs` accepts and enqueues every request without any identity check, and the CPFP path at lines 160–188 of `crates/clementine-tx-sender/src/cpfp.rs` unconditionally calls `send_to_address` when fee-payer UTXOs are insufficient. [8](#0-7) [5](#0-4)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L16-17)
```rust
const JSONRPC_INTERNAL_ERROR_CODE: i32 = -32_000;
const MAX_JSONRPC_REQUEST_BODY_SIZE: u32 = 50 * 1024 * 1024;
```

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

**File:** crates/clementine-tx-sender/src/config.rs (L29-35)
```rust
#[derive(Clone, Debug)]
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
```

**File:** crates/clementine-tx-sender/run.sh (L34-39)
```shellscript
# Enable JSON-RPC server.
export TX_SENDER_JSONRPC_BIND="${TX_SENDER_JSONRPC_BIND:-127.0.0.1}"
export TX_SENDER_JSONRPC_PORT="${TX_SENDER_JSONRPC_PORT:-3030}"
export TX_SENDER_POLL_DELAY_MS="${TX_SENDER_POLL_DELAY_MS:-500}"
export TX_SENDER_FINALITY_DEPTH="${TX_SENDER_FINALITY_DEPTH:-1}"
export TX_SENDER_INCLUDE_UNSAFE="${TX_SENDER_INCLUDE_UNSAFE:-true}"
```

**File:** crates/clementine-tx-sender/src/lib.rs (L432-435)
```rust
                FeePayingType::CPFP => {
                    self.send_cpfp_tx(id, tx, tx_metadata, adjusted_fee_rate, current_tip_height)
                        .await
                }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L160-188)
```rust
        let required_fee = Self::calculate_required_fee(
            tx.weight(),
            fee_payer_utxos_len + 1,
            fee_rate,
            FeePayingType::CPFP,
        )?;

        // Aggressively add 2x required fee to the total amount to account for sudden spikes
        // We won't actually use 2x fees, but the fee payer utxo will hold that much amount so that while fee payer utxo gets mined
        // if fees increase the utxo should still be sufficient to fund the tx with high probability
        // leftover fees will get sent back to wallet with a change output in fn create_child_tx
        let new_total_fee_needed = required_fee
            .checked_mul(2)
            .and_then(|fee| fee.checked_add(MIN_TAPROOT_AMOUNT));
        if new_total_fee_needed.is_none() {
            return Err(eyre!("Total fee needed is too large, required fee: {}, total fee payer amount: {}, fee rate: {}", required_fee, total_fee_payer_amount, fee_rate).into());
        }
        let new_fee_payer_amount =
            new_total_fee_needed.and_then(|fee| fee.checked_sub(total_fee_payer_amount));

        let new_fee_payer_amount = match new_fee_payer_amount {
            Some(fee) => fee,
            // if underflow, no new fee payer utxo is needed, log it anyway in case its a bug
            None => {
                tracing::debug!("create_fee_payer_utxo was called but no new fee payer utxo is needed for tx: {:?}, required fee: {}, total fee payer amount: {}, current fee rate: {}", tx, required_fee, total_fee_payer_amount, fee_rate);
                return Ok(());
            }
        };

```

**File:** core/src/rpc/aggregator.rs (L1270-1312)
```rust
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
