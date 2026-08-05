[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/src/cluster_info_vote_listener.rs (L1-53)
```rust
use {
    crate::{
        banking_trace::BankingPacketSender,
        consensus::vote_stake_tracker::VoteStakeTracker,
        optimistic_confirmation_verifier::OptimisticConfirmationVerifier,
        replay_stage::DUPLICATE_THRESHOLD,
        result::{Error, Result},
        sigverify_stage::GossipSigVerifyHandle,
    },
    agave_banking_stage_ingress_types::BankingPacketBatch,
    agave_votor_messages::{VerifiedVoterSlotsSender, migration::MigrationStatus},
    crossbeam_channel::{Receiver, RecvTimeoutError, Select, Sender, unbounded},
    log::*,
    solana_clock::{BankId, Slot},
    solana_gossip::{
        cluster_info::{ClusterInfo, GOSSIP_SLEEP_MILLIS},
        crds::Cursor,
    },
    solana_hash::Hash,
    solana_ledger::blockstore::Blockstore,
    solana_measure::measure::Measure,
    solana_perf::packet::{self, PacketBatch},
    solana_pubkey::Pubkey,
    solana_rpc::{
        optimistically_confirmed_bank_tracker::{BankNotification, BankNotificationSenderConfig},
        rpc_subscriptions::RpcSubscriptions,
    },
    solana_runtime::{
        bank::Bank,
        bank_forks::{BankForks, SharableBanks},
        commitment::VOTE_THRESHOLD_SIZE,
        epoch_stakes::VersionedEpochStakes,
        vote_sender_types::{ReplayVoteMessage, ReplayVoteReceiver},
    },
    solana_signature::Signature,
    solana_time_utils::AtomicInterval,
    solana_transaction::Transaction,
    solana_vote::{
        vote_parser::{self, ParsedVote},
        vote_transaction::VoteTransaction,
    },
    std::{
        cmp::max,
        collections::{HashMap, hash_map::Entry},
        iter::repeat,
        sync::{
            Arc, RwLock,
            atomic::{AtomicBool, Ordering},
        },
        thread::{self, Builder, JoinHandle, sleep},
        time::{Duration, Instant},
    },
};
```

**File:** bls-sigverify/src/bls_vote_sigverify.rs (L209-220)
```rust
/// Sig verifies `unverified_votes` and returns a `Vec` of votes that passed verification.
fn verify_votes(
    max_validators: usize,
    vote_payload_to_sign: VotePayloadToSign,
    unverified_votes: Vec<UnverifiedVotePayload>,
    stats: &mut SigVerifyVoteStats,
    banlist: &SimpleQosBanlist,
    thread_pool: &ThreadPool,
) -> Vec<VerifiedVotePayload> {
    // Try optimistic verification - fast to verify, but cannot identify invalid votes
    let res = verify_votes_optimistic(vote_payload_to_sign, &unverified_votes, stats, thread_pool);

```

**File:** bls-sigverify/src/bls_vote_sigverify.rs (L253-263)
```rust
            for (sender_identity_pubkey, error) in invalid_remote_pubkeys {
                stats.banning_validator += 1;
                if banlist.ban(sender_identity_pubkey, BAN_TIMEOUT) {
                    stats.already_banned += 1;
                } else {
                    info!(
                        "bls_vote_sigverify: banned sender={sender_identity_pubkey} due to failed \
                         verification {error:?}"
                    );
                }
            }
```
