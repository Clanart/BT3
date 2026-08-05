[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/src/vote_sender_types.rs (L1-6)
```rust
use {
    crossbeam_channel::{Receiver, Sender},
    solana_clock::{BankId, Slot},
    solana_hash::Hash,
    solana_vote::vote_parser::ParsedVote,
};
```

**File:** runtime/src/vote_sender_types.rs (L59-60)
```rust
pub type ReplayVoteSender = Sender<ReplayVoteMessage>;
pub type ReplayVoteReceiver = Receiver<ReplayVoteMessage>;
```

**File:** core/src/banking_stage/consume_worker.rs (L2971-2972)
```rust
        let (replay_vote_sender, replay_vote_receiver) = bounded(1024);
        let committer = Committer::new(None, replay_vote_sender, None);
```

**File:** ledger/src/blockstore_processor.rs (L1426-1465)
```rust
        if let Some(replay_vote_sender) = replay_vote_sender {
            let message_hashes = unverified_signatures.vote_transaction_message_hashes();
            if !message_hashes.is_empty() {
                let _ = replay_vote_sender.send(ReplayVoteMessage::Verified {
                    replay_bank_id: bank_id,
                    replay_slot: slot,
                    message_hashes,
                });
            }
        }
    } else {
        let replay_vote_sender = replay_vote_sender.cloned();
        progress.async_verification().spawn(
            replay_tx_thread_pool,
            poh_verify_elapsed,
            transaction_verify_elapsed,
            move || {
                let verification_start = Instant::now();
                let error = unverified_signatures
                    .verify()
                    .map_err(BlockstoreProcessorError::from)
                    .err();
                if let Some(err) = &error {
                    warn!("Ledger transaction signature verification failed at slot {slot}: {err}");
                    if let Some(replay_vote_sender) = &replay_vote_sender {
                        let _ = replay_vote_sender.send(ReplayVoteMessage::InvalidBank {
                            replay_bank_id: bank_id,
                            replay_slot: slot,
                        });
                    }
                } else if let Some(replay_vote_sender) = &replay_vote_sender {
                    let message_hashes = unverified_signatures.vote_transaction_message_hashes();
                    if !message_hashes.is_empty() {
                        let _ = replay_vote_sender.send(ReplayVoteMessage::Verified {
                            replay_bank_id: bank_id,
                            replay_slot: slot,
                            message_hashes,
                        });
                    }
                }
```

**File:** ledger/src/blockstore_processor.rs (L4486-4486)
```rust
        let (replay_vote_sender, replay_vote_receiver) = bounded(1024);
```

**File:** core/src/replay_stage/tests.rs (L1213-1213)
```rust
    let (replay_vote_sender, replay_vote_receiver) = bounded(1024);
```

**File:** runtime/src/bank_utils.rs (L43-76)
```rust
pub fn find_and_send_votes(
    sanitized_txs: &[impl TransactionWithMeta],
    commit_results: &[TransactionCommitResult],
    vote_sender: Option<&ReplayVoteSender>,
    send_type: ReplayVoteSendType,
) {
    if let Some(vote_sender) = vote_sender {
        sanitized_txs
            .iter()
            .zip(commit_results.iter())
            .for_each(|(tx, commit_result)| {
                if tx.is_simple_vote_transaction()
                    && commit_result.was_executed_successfully()
                    && let Some(parsed_vote) = vote_parser::parse_sanitized_vote_transaction(tx)
                    && parsed_vote.1.last_voted_slot().is_some()
                {
                    let vote = match send_type {
                        ReplayVoteSendType::VerifiedExecuted => {
                            ReplayVoteMessage::VerifiedExecuted(parsed_vote)
                        }
                        ReplayVoteSendType::Executed {
                            replay_bank_id,
                            replay_slot,
                        } => ReplayVoteMessage::Executed {
                            replay_bank_id,
                            replay_slot,
                            message_hash: *tx.message_hash(),
                            parsed_vote,
                        },
                    };
                    let _ = vote_sender.send(vote);
                }
            });
    }
```

**File:** core/src/banking_stage/committer.rs (L95-101)
```rust
        let ((), find_and_send_votes_us) = measure_us!({
            bank_utils::find_and_send_votes(
                batch.sanitized_transactions(),
                &commit_results,
                Some(&self.replay_vote_sender),
                ReplayVoteSendType::VerifiedExecuted,
            );
```
