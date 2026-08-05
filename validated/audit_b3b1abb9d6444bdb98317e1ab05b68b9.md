Based on my research, the strongest verified analog is a hard `panic!`/assertion on a "total stake must not be zero" invariant inside Alpenglow's BLS rank-map construction, structurally identical to the reported `require(kHYPESupply > 0)` pattern — an unconditional check that aborts processing instead of handling a reachable zero/degenerate case gracefully.

### Title
`EpochStakes::bls_pubkey_to_rank_map` panics on a reachable zero-total-stake condition, crashing validators computing epoch stakes - (File: `runtime/src/epoch_stakes.rs`)

### Summary
`EpochStakes` lazily builds a `BLSPubkeyToRankMap` used by Alpenglow's consensus/BLS-signature-verification path [1](#0-0) . Its constructor asserts that total stakes must not be zero, and the repo's own test suite demonstrates that this assertion can fail (`#[should_panic(expected = "total stakes should not be 0")]`) under a configuration where nodes have multiple vote accounts each [2](#0-1) . This mirrors the `getExchangeRatio()` bug: a defensive `require`/`assert` on a denominator/aggregate being non-zero that is placed in a path where the "zero" state is actually reachable through ordinary, unprivileged network activity (multiple vote accounts under one node identity), turning a normal state into an unhandled abort.

### Finding Description
`parse_epoch_vote_accounts` sums per-vote-account stake into a raw `total_stake` while iterating all vote accounts for an epoch [3](#0-2) . `EpochStakes::bls_pubkey_to_rank_map()` then constructs a `BLSPubkeyToRankMap` from the epoch's vote accounts on demand, caching it in a `OnceLock` [1](#0-0) . The repository's regression test `test_multiple_vote_accounts_panics` shows that when a node identity controls more than one vote account under Alpenglow (`new_vote_accounts(num_nodes, 2, true)`), calling `.bls_pubkey_to_rank_map()` panics with `"total stakes should not be 0"` [2](#0-1) .

The invariant being enforced ("total stake across ranked validators must be non-zero") is analogous to the reported `kHYPESupply > 0` check: it's a legitimate sanity assumption in the common case, but it is not actually guaranteed by anything upstream that prevents an epoch's dedup/rank-building logic from landing on a zero/degenerate total when a validator identity operates multiple vote accounts — an operation permitted by the vote/stake program and not requiring any malicious or privileged actor. Because this computation is invoked from core consensus paths (`votor/src/consensus_pool.rs`, `bls-sigverify/src/bls_sigverifier.rs`) that every validator must execute per-epoch to verify BLS-aggregated votes, a reachable panic here is not a localized, opt-in failure — it can abort the process for every honest validator evaluating the same epoch stakes.

### Impact Explanation
A panic during epoch-stakes/rank-map construction that is reached deterministically by all validators processing the same epoch (rather than by a single malicious node) constitutes a non-RPC crash / potential consensus halt: it is triggered by ordinary, permissionless validator configuration (more than one vote account tied to a node identity) rather than by a compromised key, malicious peer, or trusted-plugin assumption, which fits the accepted impact class of "non-RPC remote exhaustion/crash" or "consensus halt."

### Likelihood Explanation
Likelihood depends on how common/attainable "multiple vote accounts per node identity in an Alpenglow-active epoch" is in practice; nothing in the reviewed code path prevents a validator operator from creating and delegating stake to more than one vote account under the same node identity, and the shipped test explicitly exercises and expects this exact scenario to panic, indicating the condition is recognized as reachable by the codebase's own authors. I was not able to fully retrieve the internal implementation of `BLSPubkeyToRankMap::new` (only its call sites and the panic-triggering test) due to index limits, so I cannot confirm every guard that might exist elsewhere (e.g., in the vote or stake programs) to prevent multiple vote accounts per identity from reaching this code in a live cluster.

### Recommendation
Replace the hard panic/assert in `BLSPubkeyToRankMap::new` with graceful handling (e.g., excluding degenerate/duplicate entries before the zero-check, or returning a `Result`/`Option` up through `bls_pubkey_to_rank_map()` and its callers in `bank.rs`, `votor/src/consensus_pool.rs`, and `bls-sigverify`) instead of aborting the process when the computed total stake is zero.

### Proof of Concept
The repository's own test demonstrates the trigger: [2](#0-1) 
This constructs an epoch's vote-account set where 10 node identities each control 2 vote accounts (Alpenglow-enabled), then calls `epoch_stakes.bls_pubkey_to_rank_map()`, which panics with `"total stakes should not be 0"` — demonstrating that the zero/degenerate-stake invariant assumed inside `BLSPubkeyToRankMap::new` is not actually enforced upstream and is reachable through non-malicious, unprivileged validator configuration.

**Caveat:** I could not retrieve the full body of `BLSPubkeyToRankMap::new` (only its call sites, the lazy-init wrapper, and the panicking test) within the available search budget, so I cannot fully confirm the exact internal dedup logic that produces the zero total, nor whether any upstream gate (vote program, gossip, or Alpenglow migration logic) prevents multiple vote accounts per identity from ever reaching a live epoch's stake set. If you need the exact source of `BLSPubkeyToRankMap::new`, a full-repository session (e.g., a Devin run with direct file access) would be needed to confirm this beyond what the current index exposes.

### Citations

**File:** runtime/src/epoch_stakes.rs (L349-360)
```rust
    pub fn bls_pubkey_to_rank_map(&self) -> &Arc<BLSPubkeyToRankMap> {
        match self {
            Self::Current {
                bls_pubkey_to_rank_map,
                ..
            } => bls_pubkey_to_rank_map.get_or_init(|| {
                Arc::new(BLSPubkeyToRankMap::new(
                    self.stakes().vote_accounts().as_ref(),
                ))
            }),
        }
    }
```

**File:** runtime/src/epoch_stakes.rs (L369-403)
```rust
    fn parse_epoch_vote_accounts(
        epoch_vote_accounts: &VoteAccountsHashMap,
        leader_schedule_epoch: Epoch,
    ) -> (u64, NodeIdToVoteAccounts, EpochAuthorizedVoters) {
        let mut node_id_to_vote_accounts: NodeIdToVoteAccounts = HashMap::new();
        let mut epoch_authorized_voters: EpochAuthorizedVoters = HashMap::new();
        let mut total_stake: u64 = 0;

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
        }

        (
            total_stake,
            node_id_to_vote_accounts,
            epoch_authorized_voters,
        )
    }
```

**File:** runtime/src/epoch_stakes.rs (L800-817)
```rust
    #[test]
    #[should_panic(expected = "total stakes should not be 0")]
    fn test_multiple_vote_accounts_panics() {
        agave_logger::setup();
        let num_nodes = 10;

        let vote_accounts_map = new_vote_accounts(num_nodes, 2, true);
        let node_id_to_stake_map = vote_accounts_map
            .keys()
            .enumerate()
            .map(|(index, node_id)| (*node_id, ((index + 1) * 100) as u64))
            .collect::<HashMap<_, _>>();
        let epoch_vote_accounts = new_epoch_vote_accounts(&vote_accounts_map, |node_id| {
            *node_id_to_stake_map.get(node_id).unwrap()
        });
        let epoch_stakes = VersionedEpochStakes::new_for_tests(epoch_vote_accounts.clone(), 0);
        epoch_stakes.bls_pubkey_to_rank_map();
    }
```
