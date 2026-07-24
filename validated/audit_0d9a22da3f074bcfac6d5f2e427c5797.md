The attack path is real. Let me trace it precisely through the code.

**Step 1 — Unauthenticated JSON-RPC entry point**

`start_jsonrpc_server` uses `ServerBuilder::default()` with no auth middleware, no mTLS, no token, no role check. The server can bind to `0.0.0.0` (explicitly allowed by config). [1](#0-0) [2](#0-1) 

**Step 2 — Arbitrary tx accepted into the queue**

The `send_tx` handler deserializes the attacker-supplied `signed_tx_hex` and calls `insert_try_to_send` with the attacker-supplied `fee_paying_type` (CPFP). No validation of the transaction's origin, purpose, or authorization. [3](#0-2) 

**Step 3 — Loop picks it up and routes to CPFP**

`try_to_send_unconfirmed_txs` fetches all sendable txs from DB and dispatches `send_cpfp_tx` for any with `FeePayingType::CPFP`. [4](#0-3) 

**Step 4 — `InsufficientFeePayerAmount` triggers wallet spend**

For a freshly-inserted attacker tx, there are zero confirmed fee payer UTXOs. `create_package` → `create_child_tx` checks whether `minimal_non_dust + required_fee > total_fee_payer_amount + anchor_sat`. At any real fee rate this fails, returning `InsufficientFeePayerAmount`. [5](#0-4) 

`send_cpfp_tx` catches that error and calls `create_fee_payer_utxo` — **before any broadcast of the attacker's tx**. [6](#0-5) 

**Step 5 — Wallet funds are spent**

`create_fee_payer_utxo` calls `fund_raw_transaction` (selects wallet UTXOs), `sign_raw_transaction_with_wallet`, and `send_raw_transaction` — all against the tx-sender's Bitcoin Core wallet — and saves the resulting UTXO to the DB. [7](#0-6) 

**Deduplication does not protect against repeated attacks**

`insert_try_to_send` deduplicates by txid only. An attacker crafts many distinct transactions (different inputs/outputs), each with a P2A anchor output (`OP_1 OP_PUSHBYTES_2 0x4e73`), to bypass deduplication and trigger a new fee payer UTXO creation per submission. [8](#0-7) 

---

### Title
Unauthenticated JSON-RPC `send_tx` allows arbitrary CPFP fee-payer UTXO creation, draining the tx-sender wallet — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary
The tx-sender JSON-RPC server has no authentication. Any network-reachable party can submit an arbitrary Bitcoin transaction with `fee_paying_type: CPFP`. The tx-sender loop will attempt to CPFP-bump it, find no confirmed fee payer UTXOs, and call `create_fee_payer_utxo`, which funds, signs, and broadcasts a real Bitcoin transaction from the tx-sender's wallet. Repeated submissions with distinct txids drain the wallet entirely.

### Finding Description
`start_jsonrpc_server` builds a plain `jsonrpsee` server with `ServerBuilder::default()` — no mTLS, no bearer token, no IP allowlist in code. The config explicitly permits binding to `0.0.0.0`. The `send_tx` handler accepts any `InsertTryToSendParams`, including an attacker-chosen `fee_paying_type: CPFP` and an arbitrary `signed_tx_hex`. The transaction need only contain a P2A anchor output (a publicly known, anyone-can-spend script) to pass `find_p2a_vout`. On the next loop iteration, `send_cpfp_tx` is called; with no confirmed fee payer UTXOs, `create_child_tx` returns `InsufficientFeePayerAmount`, which unconditionally triggers `create_fee_payer_utxo`. That function calls `fund_raw_transaction` + `sign_raw_transaction_with_wallet` + `send_raw_transaction` against the tx-sender's Bitcoin Core wallet, spending real funds. The attacker's transaction itself never needs to be valid or broadcastable.

### Impact Explanation
The tx-sender's wallet balance is depleted. Once exhausted, the tx-sender cannot create fee payer UTXOs for legitimate bridge transactions, preventing CPFP fee-bumping of operator kickoff, payout, and reimbursement transactions. This is a direct loss of tx-sender-managed balances and a bridge liveness failure: stuck bridge transactions cannot be confirmed, blocking withdrawals and reimbursements.

### Likelihood Explanation
Any party that can reach the JSON-RPC port (network-adjacent attacker if bound to `0.0.0.0`, or any co-located process if bound to `127.0.0.1`) can exploit this with a single HTTP request. No credentials, keys, or privileged access are required. The attack is trivially repeatable with distinct crafted transactions.

### Recommendation
Add authentication to the JSON-RPC server before accepting any request. Options include: a shared secret/bearer token checked in a middleware layer, mTLS with a client certificate allowlist, or restricting the server to a Unix domain socket accessible only to trusted local processes. Additionally, validate that submitted transactions match a known bridge transaction template (e.g., verify the txid is in an allowlist populated by the bridge actors) before enqueuing them for CPFP processing.

### Proof of Concept
```rust
// Craft a tx with a P2A anchor output (anyone-can-spend, publicly known script)
let p2a_script = ScriptBuf::from_hex("51024e73").unwrap();
let attacker_tx = Transaction {
    version: Version::TWO,
    lock_time: LockTime::ZERO,
    input: vec![TxIn { previous_output: OutPoint::new(some_txid, 0), .. }],
    output: vec![
        TxOut { value: Amount::from_sat(240), script_pubkey: p2a_script },
    ],
};
// Submit via unauthenticated JSON-RPC — no credentials needed
client.insert_try_to_send(None, &attacker_tx, FeePayingType::CPFP, None, &[], &[], &[], &[]).await?;
// On next tx-sender loop iteration:
// send_cpfp_tx -> create_package -> InsufficientFeePayerAmount
// -> create_fee_payer_utxo -> fund_raw_transaction + send_raw_transaction
// Wallet balance decreases; repeat with different txids to drain completely.
```

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-88)
```rust
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

**File:** crates/clementine-tx-sender/src/lib.rs (L424-435)
```rust
            let result = match fee_paying_type {
                // Send nonstandard transactions to testnet4 using the mempool.space accelerator.
                // As mempool uses out of band payment, we don't need to do cpfp or rbf.
                _ if self.network == bitcoin::Network::Testnet4
                    && self.is_bridge_tx_nonstandard(&tx) =>
                {
                    self.send_testnet4_nonstandard_tx(&tx, id).await
                }
                FeePayingType::CPFP => {
                    self.send_cpfp_tx(id, tx, tx_metadata, adjusted_fee_rate, current_tip_height)
                        .await
                }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L207-266)
```rust
        let funded_fee_payer_tx = self
            .rpc
            .fund_raw_transaction(
                &fee_payer_bytes,
                Some(&FundRawTransactionOptions {
                    add_inputs: Some(true),
                    // for cpfp txs, the speed of tx inclusion is not that important, so we can not use unsafe utxos and wait for them to become safe. Also all cpfp fee payer tx's are safe (all wallet owned inputs), so wallet can already chain them
                    include_unsafe: Some(self.include_unsafe),
                    change_address: None,
                    change_position: None,
                    change_type: None,
                    include_watching: None,
                    lock_unspents: None,
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: Some(true),
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund cpfp fee payer tx")?
            .hex;

        let signed_fee_payer_tx: Transaction = bitcoin::consensus::deserialize(
            &self
                .rpc
                .sign_raw_transaction_with_wallet(&funded_fee_payer_tx, None, None)
                .await
                .wrap_err("Failed to sign funded tx through bitcoin RPC")?
                .hex,
        )
        .wrap_err("Failed to deserialize signed tx")?;

        let outpoint_vout = signed_fee_payer_tx
            .output
            .iter()
            .position(|o| {
                o.value == new_fee_payer_amount
                    && o.script_pubkey == self.signer.address().script_pubkey()
            })
            .ok_or(eyre!("Failed to find outpoint vout"))?;

        self.rpc
            .send_raw_transaction(&signed_fee_payer_tx)
            .await
            .wrap_err("Failed to send signed fee payer tx")?;

        self.db
            .save_fee_payer_tx(
                dbtx,
                bumped_id,
                signed_fee_payer_tx.compute_txid(),
                outpoint_vout as u32,
                new_fee_payer_amount,
                None,
            )
            .await
            .map_to_eyre()?;
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L315-318)
```rust
        if change_address.script_pubkey().minimal_non_dust() + required_fee > total_fee_payer_amount
        {
            return Err(SendTxError::InsufficientFeePayerAmount);
        }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L620-629)
```rust
            Err(SendTxError::InsufficientFeePayerAmount) => {
                self.create_fee_payer_utxo(
                    try_to_send_id,
                    None,
                    &tx,
                    fee_rate,
                    total_amount,
                    confirmed.len(),
                )
                .await?;
```

**File:** crates/clementine-tx-sender/src/client.rs (L74-80)
```rust
        let tx_exists = self
            .db
            .check_if_tx_exists_on_txsender(Some(dbtx), txid)
            .await?;
        if let Some(try_to_send_id) = tx_exists {
            return Ok(try_to_send_id);
        }
```
