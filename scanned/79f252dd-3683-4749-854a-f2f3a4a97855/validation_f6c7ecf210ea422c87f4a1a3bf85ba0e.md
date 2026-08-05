### Title
Banlist is enforced only at QUIC connection admission, not before BLS vote/certificate verification, allowing banned senders' consensus messages to still be processed - (File: `bls-sigverify/src/bls_sigverifier.rs`)

### Summary
This is the direct Agave analog of the "banned entity check exists but the wrong/insufficient predicate is consulted at the enforcement point" bug class from the Stakehouse report. In Stakehouse, `registerBLSPublicKeys` called `isBLSPublicKeyPartOfLSDNetwork` (membership only) instead of `isBLSPublicKeyBanned` (membership AND not-banned), so banned keys still passed. In Agave's BLS sigverify pipeline, a `SimpleQosBanlist::is_banned()` check exists and is correctly consulted at QUIC connection admission time [1](#0-0) , but the message-processing pipeline that consumes votes and certificates (`extract_and_filter_msgs`, `keep_vote`, `add_certificate_to_group`) never calls `is_banned()` at all — it only ever calls `.ban()` to punish a sender after detecting an invalid message [2](#0-1) . As a result, a pubkey that is already on the banlist can still have its votes/certificates fully verified and forwarded into the consensus vote pool.

### Finding Description
`SimpleQosBanlist` is a shared structure used both by the QUIC streamer (`streamer/src/nonblocking/simple_qos.rs`) and by the BLS sigverifier (`bls-sigverify/src/bls_sigverifier.rs`) via `SigVerifierContext::banlist` [3](#0-2) . The intent (as shown by `BAN_TIMEOUT` comment) is that "if we receive an invalid certificate or vote, we ban its attributed sender" for 48 hours [4](#0-3) .

However, grepping the entire sigverify pipeline (`bls_sigverifier.rs`, `bls_vote_sigverify.rs`, `bls_cert_sigverify.rs`) shows `is_banned()` is never called anywhere in these files — only `.ban()` is called (to add an offender to the list after failure), in three call sites:
- `bls_cert_sigverify.rs::handle_cert_verify_error` [5](#0-4) 
- `bls_sigverifier.rs::keep_vote` on `VotePoolError::Invalid` [6](#0-5) 
- `bls_vote_sigverify.rs::verify_votes` on individual-verification failure [7](#0-6) 

The only place `is_banned()` is actually checked is `SimpleQos::try_add_connection`, which gates whether a new QUIC connection is admitted [1](#0-0) . This is a connection-admission-time check, not a message-processing-time check. Once a connection to a since-banned pubkey already exists (banned mid-session), the eviction is best-effort via an async eviction channel with bounded capacity (`MAX_IN_FLIGHT_EVICTIONS = 2_000`) that can silently drop eviction requests under load [8](#0-7) , and packets already queued in `packet_receiver`/`certificate_receiver` before eviction completes are still drained and processed by `verify_and_send_inputs` with no re-check of the banlist [9](#0-8) .

More significantly, the second input source — certificates sourced from the blockstore and attributed to the historical slot leader (not a live network connection at all) — has *no* QUIC connection whatsoever to gate:
```
bls-sigverify/src/bls_sigverifier.rs:352-359
let Some(sender_identity_pubkey) = self
    .leader_schedule
    .slot_leader_at(carrier_slot, Some(root_bank))
    .map(|leader| leader.id)
else { continue; };
self.add_certificate_to_group(&mut cert_groups, certificate, sender_identity_pubkey);
``` [10](#0-9) 
This path never touches `SimpleQosBanlist` at all, so even if the attributed leader pubkey is currently banned, its certificates from blockstore are still admitted into `cert_groups` and verified/forwarded by `verify_and_send_certificates` [11](#0-10) .

### Impact Explanation
A validator identity that has already been banned for sending an invalid BLS vote or certificate (per the documented 48-hour ban intent) can still have its votes and certificates from the blockstore-leader-attribution path (or from a pre-existing/racing QUIC connection) accepted into `extract_and_filter_msgs`, verified, and pushed into the vote pool / certificate pool that feeds consensus (`channel_to_pool`, `VotePool`). This weakens the anti-spam/anti-abuse guarantee the banlist is meant to provide for the BLS consensus-message path — a component in the votor/consensus pipeline (RPC/pubsub, gossip, runtime, and consensus-adjacent code are all in scope), and could allow a previously-flagged-invalid sender's messages to keep being processed and consuming verification/consensus resources, rather than being immediately discarded as intended.

### Likelihood Explanation
No malicious peer/validator collusion or privileged access is required beyond the attacker already being an unprivileged network participant whose pubkey was banned — this is exactly the scenario the ban mechanism is supposed to close. The gap is structural (missing `is_banned()` call at the vote/cert intake and extraction stage), not timing-dependent for the blockstore-cert-leader-attribution path, which has zero interaction with the QUIC banlist by design.

### Recommendation
Add an explicit `self.banlist.is_banned(&sender_identity_pubkey)` check in `extract_and_filter_msgs` (for both the packet-derived `sender_identity_pubkey` and the leader-schedule-derived `sender_identity_pubkey` used for blockstore certificates) before adding a vote/certificate to `votes`/`cert_groups`, mirroring the check already present in `SimpleQos::try_add_connection`.

### Proof of Concept
1. A validator's identity pubkey is added to `SimpleQosBanlist` (e.g., via `keep_vote`'s `VotePoolError::Invalid` path banning it for `BAN_TIMEOUT`) [6](#0-5) .
2. That pubkey is later the slot leader for some `carrier_slot` still within the verification window; a certificate for that slot arrives via the blockstore-sourced `certificates` input.
3. `extract_and_filter_msgs` resolves `sender_identity_pubkey` via `leader_schedule.slot_leader_at(...)` and calls `add_certificate_to_group` without ever consulting `self.banlist.is_banned(...)` [12](#0-11) .
4. The certificate is verified and forwarded through `verify_and_send_certificates` into the consensus pool, exactly as if the sender had never been banned.

### Citations

**File:** streamer/src/nonblocking/simple_qos.rs (L66-87)
```rust
    /// Ban the `pubkey` for the specified `timeout`
    ///
    /// Returns `true` if the `id` was already banned else `false`.
    pub fn ban(&self, pubkey: Pubkey, timeout: Duration) -> bool {
        let ret = self.banlist.ban(pubkey, timeout);
        match self.eviction_sender.try_send(pubkey) {
            Ok(()) => {}
            Err(TrySendError::Full(pubkey)) => {
                error!(
                    "Simple QoS banlist eviction queue full, dropping eviction request for \
                     {pubkey}"
                );
            }
            Err(TrySendError::Closed(pubkey)) => {
                info!(
                    "Simple QoS banlist eviction queue closed, dropping eviction request for \
                     {pubkey}"
                );
            }
        }
        ret
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L294-308)
```rust
        async move {
            const PRUNE_RANDOM_SAMPLE_SIZE: usize = 2;
            let remote_pubkey = conn_context.remote_pubkey()?;
            if self.banlist.is_banned(&remote_pubkey) {
                let remote_address = conn_context.remote_address;
                info!("Rejecting banned pubkey {remote_pubkey} from {remote_address:?}");
                self.stats
                    .connection_add_failed_banned
                    .fetch_add(1, Ordering::Relaxed);
                connection.close(
                    CONNECTION_CLOSE_CODE_DISALLOWED.into(),
                    CONNECTION_CLOSE_REASON_DISALLOWED,
                );
                return None;
            }
```

**File:** bls-sigverify/src/bls_sigverifier.rs (L56-59)
```rust
/// If we receive an invalid certificate or vote, we ban its attributed sender. For certificates
/// received from blockstore, that sender is the scheduled leader for the carrier slot. We ban the
/// sender for 2 days, which roughly corresponds to an epoch.
pub(super) const BAN_TIMEOUT: Duration = Duration::from_hours(48);
```

**File:** bls-sigverify/src/bls_sigverifier.rs (L63-71)
```rust
pub struct SigVerifierContext {
    pub migration_status: Arc<MigrationStatus>,
    pub banlist: Arc<SimpleQosBanlist>,
    pub sharable_banks: SharableBanks,
    pub cluster_info: Arc<ClusterInfo>,
    pub leader_schedule: Arc<LeaderScheduleCache>,
    pub num_threads: usize,
    pub generated_cert_types: Arc<GeneratedCertTypes>,
}
```

**File:** bls-sigverify/src/bls_sigverifier.rs (L194-239)
```rust
    fn verify_and_send_inputs(
        &mut self,
        batches: Vec<PacketBatch>,
        certificates: Vec<(Slot, UnverifiedCertificate)>,
    ) -> Result<(), SigVerifyError> {
        let root_bank = self.sharable_banks.root();
        self.maybe_prune_caches(&root_bank);

        let (extracted_msgs, extract_msgs_us) =
            measure_us!(self.extract_and_filter_msgs(batches, certificates, &root_bank));
        self.stats
            .extract_filter_msgs_us
            .add_sample(extract_msgs_us);

        let (votes_result, certs_result) = self.thread_pool.join(
            || {
                verify_and_send_votes(
                    extracted_msgs.votes,
                    &self.rank_map_cache,
                    &root_bank,
                    &self.cluster_info,
                    &self.leader_schedule,
                    &self.banlist,
                    &self.thread_pool,
                    &self.channels,
                )
            },
            || {
                verify_and_send_certificates(
                    &mut self.verified_certs,
                    extracted_msgs.certs,
                    &root_bank,
                    &self.channels.channel_to_pool,
                    &self.banlist,
                    &self.thread_pool,
                )
            },
        );

        let vote_stats = votes_result?;
        let cert_stats = certs_result?;

        self.stats.vote_stats.merge(vote_stats);
        self.stats.cert_stats.merge(cert_stats);
        Ok(())
    }
```

**File:** bls-sigverify/src/bls_sigverifier.rs (L333-359)
```rust
        for (carrier_slot, certificate) in certificates {
            let is_genesis = matches!(&certificate.cert_type, CertificateType::Genesis(_));
            let is_active = if is_genesis {
                // Genesis certificates from blockstore are only allowed when we are in migration
                self.migration_status.is_in_migration()
            } else {
                self.migration_status
                    .should_allow_block_markers(carrier_slot)
            };
            if carrier_slot < root_slot
                || certificate.shred_version != my_shred_version
                || !is_active
            {
                continue;
            }
            if certificate.cert_type.slot() < root_slot {
                self.stats.num_old_certs_received += 1;
                continue;
            }
            let Some(sender_identity_pubkey) = self
                .leader_schedule
                .slot_leader_at(carrier_slot, Some(root_bank))
                .map(|leader| leader.id)
            else {
                continue;
            };
            self.add_certificate_to_group(&mut cert_groups, certificate, sender_identity_pubkey);
```

**File:** bls-sigverify/src/bls_sigverifier.rs (L423-444)
```rust
        match self.vote_pool.try_add_vote(&msg, rank, rank_map.len()) {
            Ok(()) => Some(UnverifiedVotePayload {
                vote_message: msg,
                sender_bls_pubkey: entry.bls_pubkey,
                sender_vote_account_pubkey: entry.vote_account_pubkey,
                sender_identity_pubkey,
                stake: entry.stake,
                rank,
            }),
            Err(VotePoolError::Duplicate) => None,
            Err(VotePoolError::Invalid) => {
                self.stats.invalid_vote_banning_validator += 1;
                if self.banlist.ban(sender_identity_pubkey, BAN_TIMEOUT) {
                    self.stats.invalid_vote_already_banned += 1;
                } else {
                    info!(
                        "bls_sigverifier: banned sender={sender_identity_pubkey} due to invalid \
                         vote"
                    );
                }
                None
            }
```

**File:** bls-sigverify/src/bls_cert_sigverify.rs (L161-178)
```rust
fn handle_cert_verify_error(
    err: CertVerifyError,
    sender_identity_pubkey: Pubkey,
    stats: &mut SigVerifyCertStats,
    banlist: &SimpleQosBanlist,
) {
    match &err {
        CertVerifyError::CertVerifyFailed(_) => {
            stats.banning_validator += 1;
            if banlist.ban(sender_identity_pubkey, BAN_TIMEOUT) {
                stats.already_banned += 1;
            } else {
                info!(
                    "bls_cert_sigverify: banned sender={sender_identity_pubkey} due to error {err}"
                );
            }
            stats.certificate_verification_failed += 1;
        }
```

**File:** bls-sigverify/src/bls_vote_sigverify.rs (L253-262)
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
```
