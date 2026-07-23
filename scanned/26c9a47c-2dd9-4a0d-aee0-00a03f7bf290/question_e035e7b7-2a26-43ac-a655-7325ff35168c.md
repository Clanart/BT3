[File: 'crates/clementine-tx-sender/src/confirmations.rs -> Scope: Critical. An unprivileged attacker can make Clementine release, redirect, double-spend, or permanently lock bridged BTC, operator collateral, reimbursement outputs, or bridge-controlled UTXOs.'] [Symbol: sync_outpoint_observations_via_rpc / list_unfinalized_cancel_outpoints and list_unfinalized_activate_outpoints called without DB transaction] Can the absence of a wrapping DB transaction across the sequential calls to list_unfinalized_cancel_outpoints and list_unfinalized_activate_outpoints (both called with dbtx=

### Citations

**File:** crates/clementine-tx-sender/src/confirmations.rs (L1-50)
```rust
use crate::{
    rpc_errors::is_mempool_not_found_error, rpc_errors::is_not_found_error, FeePayingType,
    TxSender, TxSenderTransaction,
};
use bitcoin::{BlockHash, Network, OutPoint, Txid};
use bitcoincore_rpc::RpcApi;
use clementine_errors::BridgeError;
use clementine_extended_rpc::{BitcoinRPCError, RetryConfig};
use serde::Deserialize;
use std::collections::HashMap;
use tokio::time::{timeout, Duration};
use tokio_retry::RetryIf;

#[derive(Copy, Clone, Debug)]
enum TxChainStatus {
    /// Confirmed in active chain with N confirmations at a specific block height.
    Confirmed {
        block_height: u32,
        confirmations: u32,
    },
    /// Present in the mempool (verified via `getmempoolentry`) but not yet confirmed.
    InMempool,
    /// Neither in mempool nor in the active chain.
    NotPresent,
}

#[derive(Clone, Debug, Deserialize)]
struct MempoolOutspendStatus {
    confirmed: bool,
    block_height: Option<u32>,
    block_hash: Option<BlockHash>,
}

#[derive(Clone, Debug, Deserialize)]
struct MempoolOutspendResponse {
    spent: bool,
    txid: Option<Txid>,
    vin: Option<u32>,
    status: Option<MempoolOutspendStatus>,
}

#[derive(Clone, Debug)]
struct ValidatedOutspend {
    confirmed: bool,
    confirmations: u32,
    block_height: Option<u32>,
}

impl TxSender {
    /// Synchronize tx-sender confirmation/spent tracking using Bitcoin RPC.
```

**File:** crates/clementine-tx-sender/src/confirmations.rs (L381-405)
```rust
    async fn sync_outpoint_observations_via_rpc(
        &self,
        mut dbtx: Option<&mut TxSenderTransaction>,
        start_tip_height: u32,
    ) -> Result<(), BridgeError> {
        let finality = self.finality_depth;

        // Compute observed_tip_height early so we can use it for finality checks on outpoints.
        let end_tip_height = self
            .rpc
            .get_current_chain_height()
            .await
            .map_err(|e| BridgeError::Eyre(eyre::eyre!(e)))?;
        let observed_tip_height = std::cmp::max(start_tip_height, end_tip_height);

        async fn check_spent(
            rpc: &clementine_extended_rpc::ExtendedBitcoinRpc,
            outpoint: &OutPoint,
        ) -> Result<Option<bool>, BitcoinRPCError> {
            match rpc.is_utxo_spent(outpoint).await {
                Ok(spent) => Ok(Some(spent)),
                Err(BitcoinRPCError::TransactionNotConfirmed) => Ok(None),
                Err(e) => Err(e),
            }
        }
```

**File:** crates/clementine-tx-sender/src/confirmations.rs (L582-728)
```rust
    async fn get_mempool_outspend(&self, outpoint: &OutPoint) -> Option<ValidatedOutspend> {
        let host = self.mempool_config.host.as_ref()?;

        let url = match mempool_outspend_url(host, self.network, outpoint) {
            Ok(url) => url,
            Err(e) => {
                tracing::warn!(%outpoint,
