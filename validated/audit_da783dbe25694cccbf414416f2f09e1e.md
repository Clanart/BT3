## Title
Unbounded growth of `latest_vote_slot_per_validator` in `ClusterInfoVoteListener::process_votes_loop` (missing pruning on root progress) - (File: `core/src/cluster_info_vote_listener.rs`)

### Summary
`process_votes_loop` maintains a `HashMap<Pubkey, Slot>` named `latest_vote_slot_per_validator` that is created once per validator process lifetime and only ever grows via `.entry(*vote_pubkey).or_insert(0)`. Unlike the two sibling structures maintained in the very same loop iteration — `vote_tracker.progress_with_new_root_bank(&root_bank)` and `replay_vote_buffer.prune_stale_slots(root_bank.slot())` — there is no corresponding pruning call for `latest_vote_slot_per_validator` anywhere in the loop or elsewhere in the file. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
Every processed vote (gossip or replay) that passes `filter_verified_votes`/`track_new_votes_and_notify_confirmations` inserts a new entry for its `vote_pubkey` into `latest_vote_slot_per_validator` if one doesn't already exist: [4](#0-3) 

This entry is subsequently only ever updated (`*latest_vote_slot = max(*latest_vote_slot, last_vote_slot);`) and is never removed, even when the validator becomes inactive, its vote account is closed, or its stake is withdrawn. This is a genuine asymmetry with the surrounding code: on the same root-progress boundary, `vote_tracker.progress_with_new_root_bank(&root_bank)` prunes old `SlotVoteTracker`s and `replay_vote_buffer.prune_stale_slots(root_bank.slot())` prunes stale buffered votes, but no equivalent call exists for `latest_vote_slot_per_validator`.

However, the entry-point gating this map is `filter_verified_votes`, which requires the voting pubkey to be present in `root_bank.epoch_stakes(epoch)?.epoch_authorized_voters()`: [5](#0-4) 

`epoch_authorized_voters` is built by `VersionedEpochStakes::parse_epoch_vote_accounts`, which explicitly skips any vote account with **zero delegated stake**: [6](#0-5) 

So the premise in the question — "permissionless vote-account creation" with **zero** stake casting votes that get counted — does not hold: a vote from a zero-stake vote account never reaches `epoch_authorized_voters`, is filtered out by `filter_verified_votes`, and never inserts into `latest_vote_slot_per_validator`. An attacker must actually have nonzero delegated stake (subject to the stake program's minimum delegation and stake activation warm-up) behind each distinct vote-account pubkey that is to be inserted into the map.

That said, the underlying map-growth defect is real: once a vote pubkey with nonzero stake has voted, its entry lives forever in this process-lifetime `HashMap`, regardless of whether the stake is later deactivated/withdrawn or the vote account closed. An attacker with a fixed amount of capital can re-delegate the *same* stake account to a fresh vote-account pubkey every epoch (waiting for stake activation each time) and cast one vote per epoch, permanently minting a new ~40–80 byte entry (`Pubkey` + `Slot` + hashmap bucket overhead) in every running validator's local map without ever losing the underlying stake.

### Impact Explanation
The map lives for the lifetime of the `solCiProcVotes` thread and is per-process (not persisted/checkpointed), so growth is a memory leak that requires a validator restart to reclaim. However, because insertion is gated behind real, nonzero delegated stake and epoch-boundary stake activation, the rate of growth is bounded by real economic/timing constraints (minimum delegation amount, one epoch ≈ 2–3 days for stake activation) rather than being a cheap, high-rate remote exhaustion primitive. Reaching gigabyte-scale memory growth via this vector alone (millions of entries) would require an attacker to sustain very large numbers of distinct staked/re-delegated vote-account identities over a long period, which is a far weaker attack than typical "single unprivileged low-rate" DoS primitives in scope.

### Likelihood Explanation
Low. The premise of the submitted question (zero-stake, freely-created vote accounts inflating the map) is incorrect because `filter_verified_votes`/`epoch_authorized_voters` reject unstaked vote accounts before they ever reach `track_new_votes_and_notify_confirmations`. A real attack requires continuously funding/re-delegating stake and waiting for epoch-boundary activation, which is slow and capital-intensive, making this an unbounded-but-slow-growth code defect rather than a practical crash/DoS vector at the severity level required by the bounty scope.

### Recommendation
Even though practical exploitability is limited, the missing pruning is a genuine code defect that should be fixed defensively: prune `latest_vote_slot_per_validator` alongside the existing root-progress pruning calls (e.g., drop entries for pubkeys no longer present in the current epoch's `epoch_authorized_voters`/stake set), mirroring the pattern already used by `vote_tracker.progress_with_new_root_bank` and `replay_vote_buffer.prune_stale_slots` at `core/src/cluster_info_vote_listener.rs:645-646`.

### Proof of Concept
1. Set up a local cluster/test harness with the ability to create stake + vote accounts.
2. In a loop over N epochs: create a new vote account, delegate a stake account (reused across iterations) to it, wait for activation, submit one vote transaction from it, then optionally close the vote account.
3. Instrument `ClusterInfoVoteListener::process_votes_loop`/`latest_vote_slot_per_validator` (e.g., via a test harness calling `listen_and_confirm_votes` directly, as done in `run_test_process_votes`) and observe that its `.len()` grows by one per distinct vote pubkey and never decreases, even after the corresponding stake is deactivated/withdrawn and root progresses past those slots — in contrast to `vote_tracker`'s pruned `slot_vote_trackers`. [7](#0-6)

### Citations

**File:** core/src/cluster_info_vote_listener.rs (L596-607)
```rust
            .filter_map(|(tx, packet_batch)| {
                let (vote_account_key, vote, ..) = vote_parser::parse_vote_transaction(&tx)?;
                let slot = vote.last_voted_slot()?;
                let epoch = epoch_schedule.get_epoch(slot);
                let authorized_voter = root_bank
                    .epoch_stakes(epoch)?
                    .epoch_authorized_voters()
                    .get(&vote_account_key)?;
                let mut keys = tx.message.account_keys.iter().enumerate();
                if !keys.any(|(i, key)| tx.message.is_signer(i) && key == authorized_voter) {
                    return None;
                }
```

**File:** core/src/cluster_info_vote_listener.rs (L624-627)
```rust
        let mut latest_vote_slot_per_validator = HashMap::new();
        let mut last_process_root = Instant::now();
        let mut vote_processing_time = Some(VoteProcessingTiming::default());
        let mut replay_vote_buffer = VoteBuffer::new();
```

**File:** core/src/cluster_info_vote_listener.rs (L645-646)
```rust
                vote_tracker.progress_with_new_root_bank(&root_bank);
                replay_vote_buffer.prune_stale_slots(root_bank.slot());
```

**File:** core/src/cluster_info_vote_listener.rs (L824-828)
```rust
        let (last_vote_slot, last_vote_hash) = vote.last_voted_slot_hash().unwrap();

        let latest_vote_slot = latest_vote_slot_per_validator
            .entry(*vote_pubkey)
            .or_insert(0);
```

**File:** core/src/cluster_info_vote_listener.rs (L1321-1354)
```rust
        let mut latest_vote_slot_per_validator = HashMap::new();

        let gossip_vote_slots = vec![1, 2];
        let replay_vote_slots = vec![3, 4];
        send_vote_txs(
            gossip_vote_slots.clone(),
            replay_vote_slots.clone(),
            &validator_voting_keypairs,
            hash,
            &votes_txs_sender,
            &replay_votes_sender,
        );

        // Check that all the votes were registered for each validator correctly
        let notifiers = ConfirmationNotifiers {
            gossip_verified_vote_hash_sender: gossip_verified_vote_hash_sender.clone(),
            verified_voter_slots_sender: verified_voter_slots_sender.clone(),
            rpc_subscriptions: Some(subscriptions.clone()),
            bank_notification_sender: None,
            duplicate_confirmed_slot_sender: None,
            migration_status: Arc::new(MigrationStatus::default()),
        };
        let mut replay_vote_buffer = VoteBuffer::new();
        ClusterInfoVoteListener::listen_and_confirm_votes(
            &votes_txs_receiver,
            &vote_tracker,
            &bank0,
            &replay_votes_receiver,
            &mut replay_vote_buffer,
            &notifiers,
            &mut None,
            &mut latest_vote_slot_per_validator,
        )
        .unwrap();
```

**File:** runtime/src/epoch_stakes.rs (L377-395)
```rust
        for (key, (stake, account)) in epoch_vote_accounts.iter() {
            total_stake += *stake;

            if *stake == 0 {
                continue;
            }

            let vote_state = account.vote_state_view();

            if let Some(authorized_voter) = vote_state.get_authorized_voter(leader_schedule_epoch) {
                let node_vote_accounts = node_id_to_vote_accounts
                    .entry(*vote_state.node_pubkey())
                    .or_default();

                node_vote_accounts.total_stake += stake;
                node_vote_accounts.vote_accounts.push(*key);

                epoch_authorized_voters.insert(*key, *authorized_voter);
            }
```
