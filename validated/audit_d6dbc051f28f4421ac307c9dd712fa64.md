## Title
Unbounded free CPU exhaustion of TPU-vote sigverify workers via unauthenticated packets bypassing the priority-floor spam gate - (File: `core/src/sigverify.rs`, `core/src/sigverify_stage.rs`)

## Summary
The Aleo report describes a DOS primitive where an attacker can force expensive cryptographic verification (a SNARK proof check) to run for free because the transaction type in question carries no fee commitment that can be seized on failure — cheap-to-produce, expensive-to-verify garbage is accepted into the verification pipeline with no cost gate ahead of the costly step. The closest structural analog in Agave is in the TPU sigverify pipeline: the non-vote lane has a "priority floor" cost-gate that can cheaply reject low-value packets *before* running the expensive `ed25519_verify_serial` signature check, but the TPU-vote lane explicitly disables this gate, so every non-duplicate vote-shaped packet reaching the vote port gets full signature verification for free.

## Finding Description
`SigVerifyWorkerState` carries an optional `priority_floor: Option<Arc<SchedulerPriorityFloor>>` that, when saturated, lets the scheduler publish a priority floor so sigverify can drop below-floor packets ahead of signature verification [1](#0-0) . In `run_transaction_task`, packets first pass through the deduper, and then — only if `state.priority_floor` is `Some`, and only when the floor is `> 0` (i.e. the non-vote scheduler is saturated) — `apply_priority_floor_to_batch` is used to mark low-priority packets as discarded *before* the CPU-expensive `sigverify::ed25519_verify_serial` call runs [2](#0-1) .

When the `SigVerifyWorkerPool` is constructed, the TPU-vote worker state is built with `None` for this parameter, with the comment "votes are not dropped for priority-floor" [3](#0-2) . This means every packet arriving on the `tpu_vote_receiver` channel that survives the bloom-filter deduper unconditionally proceeds straight to full `ed25519_verify_serial` — there is no cost/fee/priority gate of any kind ahead of the expensive verification step for this lane.

`ed25519_verify_serial` performs genuine ed25519 signature verification math over the packet batch, which is computationally far more expensive than producing a syntactically valid-looking vote packet with an arbitrary (invalid) signature. An attacker does not need a valid signature, a real stake, or any payment — they only need packets that are (a) distinct enough to evade the deduper's bloom filter (`Deduper<2, [u8]>`, a probabilistic content-hash filter, easily defeated by varying a few bytes such as the bogus signature or nonce fields) and (b) shaped enough to not be rejected before reaching the verify call. Unlike the non-vote lane, there is no mechanism analogous to "check the fee payer can pay, or the sender has enough stake/priority" gating this expensive step for votes.

This mirrors the Aleo root cause precisely: a class of network input (there: `split` transactions without a fee-carrying escape valve; here: TPU-vote-shaped packets) is exempted from the cost-gate that every other class of input in the same pipeline enjoys, allowing cheap junk to force expensive verification with no economic or reputational cost to the sender.

## Impact Explanation
This falls into the "non-RPC remote exhaustion/crash" category of valid impact: an unprivileged, unauthenticated remote sender can consume validator CPU cycles in the sigverify worker pool dedicated to the TPU vote lane, without spending a fee, staking, or holding a valid key. Because these worker threads are shared with other sigverify duties are separately pooled per lane, but CPU contention on the host (and the vote sigverify thread pool itself) directly affects the validator's ability to process legitimate vote traffic promptly, which can degrade turbine/consensus vote propagation processing and block production timing under sustained load. This does not require a malicious peer/validator with cluster stake — the vote port is reachable by any external sender submitting UDP/QUIC packets.

## Likelihood Explanation
Likelihood is moderate: the deduper provides some resistance, but a bloom-filter-based deduper (`Deduper<2, [u8]>`) is easily bypassed by minor packet perturbation (e.g., different bogus signature bytes per packet), which is trivial and cheap to generate at high volume, especially since the attacker does not need any valid credentials to build these packets — they only need to shape data that parses far enough to reach `ed25519_verify_serial` (the format/legacy-`Transaction` parsing in this path is comparatively lightweight versus the signature math it protects).

## Recommendation
Apply an equivalent cost/rate gate to the TPU-vote lane before invoking `ed25519_verify_serial`, rather than unconditionally disabling the priority floor for votes. Options include: a lightweight per-source-IP/stake-weighted rate limiter ahead of the vote sigverify path (independent of the non-vote priority-floor mechanism, since votes lack an on-chain fee to prioritize by), or reintroducing a cheaper pre-filter (e.g., validating vote-account membership/stake weight from a snapshot before running full signature math) so that unstaked, unauthenticated senders cannot force unbounded expensive verification work for free.

## Proof of Concept
Conceptual (not executed, no sandbox access): 
1. Craft a batch of syntactically valid legacy `Transaction`/vote-shaped packets, each targeting the vote program, with distinct (but cryptographically invalid) signature bytes so each packet differs enough to evade the `Deduper` bloom filter.
2. Flood the TPU vote port with these packets at high rate.
3. Because `SigVerifyWorkerState` for the vote lane is constructed with `priority_floor = None` [3](#0-2) , every distinct packet bypasses the cost gate available to the non-vote lane and proceeds directly to `sigverify::ed25519_verify_serial` [4](#0-3) , consuming sigverify worker CPU with zero cost to the attacker and no dependency on holding stake, a valid key, or paying any fee.

I was not able to fully verify (due to index/tool limits) how much upstream QUIC-level rate limiting or stake-weighted connection throttling exists specifically for the TPU vote port versus the regular TPU port, which could partially mitigate this before packets even reach `sigverify.rs`; a full assessment of exploitability would require examining `core/src/tpu.rs` and the QUIC streamer's stake-weighting logic for the vote port in more depth than the available context allowed.

### Citations

**File:** core/src/sigverify.rs (L57-67)
```rust
#[derive(Clone)]
pub(crate) struct SigVerifyWorkerState {
    banking_stage_sender: BankingPacketSender,
    deduper: Arc<Deduper<2, [u8]>>,
    stats: SigVerifyWorkerStats,
    /// Scheduler-published priority floor: when saturated, the scheduler publishes
    /// the queue-min transaction's priority and workers drop at-or-below-floor
    /// arrivals here, ahead of signature verification. `None` disables the
    /// check (e.g. for the vote worker, which is governed by a separate
    /// priority policy in banking stage).
    priority_floor: Option<Arc<SchedulerPriorityFloor>>,
```

**File:** core/src/sigverify.rs (L282-331)
```rust
        let (discard_or_dedup_fail, dedup_time_us) =
            measure_us!(deduper::dedup_packets_and_count_discards(
                &state.deduper,
                std::slice::from_mut(&mut batch)
            ));
        state
            .stats
            .total_dedup
            .fetch_add(discard_or_dedup_fail as usize, Ordering::Relaxed);
        state
            .stats
            .total_dedup_time_us
            .fetch_add(dedup_time_us as usize, Ordering::Relaxed);

        if discard_or_dedup_fail as usize == batch_len {
            return true;
        }

        let working_bank = sharable_banks.working();

        if let Some(floor) = state.priority_floor.as_ref() {
            let floor = floor.get();
            if floor > 0 {
                let ((dropped, all_below), priority_floor_time_us) = measure_us!(
                    apply_priority_floor_to_batch(&mut batch, floor, &working_bank)
                );
                state
                    .stats
                    .total_priority_floor_time_us
                    .fetch_add(priority_floor_time_us as usize, Ordering::Relaxed);
                if dropped > 0 {
                    state
                        .stats
                        .total_dropped_below_priority_floor
                        .fetch_add(dropped, Ordering::Relaxed);
                }
                if all_below {
                    // Entire batch went below-floor: nothing left to verify or
                    // forward.
                    return true;
                }
            }
        }

        let enable_tx_v1 = working_bank.feature_set.snapshot().enable_tx_v1;
        let (_, verify_time_us) = measure_us!(sigverify::ed25519_verify_serial(
            &mut batch,
            reject_non_vote,
            enable_tx_v1,
        ));
```

**File:** core/src/sigverify_stage.rs (L196-219)
```rust
                scheduler_priority_floor,
            ),
            SigVerifyWorkerState::new(
                tpu_vote_sender,
                tpu_vote_deduper.clone(),
                SigVerifyWorkerStats {
                    total_batches: tpu_vote_stats.total_batches.clone(),
                    total_packets: tpu_vote_stats.total_packets.clone(),
                    total_dedup: tpu_vote_stats.total_dedup.clone(),
                    total_dedup_time_us: tpu_vote_stats.total_dedup_time_us.clone(),
                    total_valid_packets: tpu_vote_stats.total_valid_packets.clone(),
                    total_verify_time_us: tpu_vote_stats.total_verify_time_us.clone(),
                    max_pre_send_len: tpu_vote_stats.max_pre_send_len.clone(),
                    eviction_drops: tpu_vote_stats.eviction_drops.clone(),
                    total_dropped_below_priority_floor: tpu_vote_stats
                        .total_dropped_below_priority_floor
                        .clone(),
                    total_priority_floor_time_us: tpu_vote_stats
                        .total_priority_floor_time_us
                        .clone(),
                },
                None, // votes are not dropped for priority-floor
            ),
        );
```
