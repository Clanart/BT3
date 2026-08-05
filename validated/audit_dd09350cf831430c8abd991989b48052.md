[1](#0-0) [2](#0-1)

### Citations

**File:** send-transaction-service/src/send_transaction_service.rs (L1-18)
```rust
use {
    crate::{
        send_transaction_service_stats::{
            SendTransactionServiceStats, SendTransactionServiceStatsReport,
        },
        transaction_client::TpuSender,
    },
    crossbeam_channel::{Receiver, RecvTimeoutError},
    itertools::Itertools,
    log::*,
    solana_hash::Hash,
    solana_nonce_account as nonce_account,
    solana_pubkey::Pubkey,
    solana_runtime::{
        bank::Bank,
        bank_forks::{BankForks, BankPair},
    },
    solana_signature::Signature,
```

**File:** send-transaction-service/src/send_transaction_service.rs (L60-77)
```rust
pub struct SendTransactionService {
    receive_txn_thread: JoinHandle<()>,
    retry_thread: JoinHandle<()>,
    exit: Arc<AtomicBool>,
}

pub struct TransactionInfo {
    pub message_hash: Hash,
    pub signature: Signature,
    pub blockhash: Hash,
    pub wire_transaction: Vec<u8>,
    pub last_valid_block_height: u64,
    pub durable_nonce_info: Option<(Pubkey, Hash)>,
    pub max_retries: Option<usize>,
    retries: usize,
    /// Last time the transaction was sent
    last_sent_time: Option<Instant>,
}
```
