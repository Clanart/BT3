## Analysis

The Sui report's broken invariant is: **an attacker-growable, permissionlessly-created collection is iterated in full inside a single operation with no cap**, turning a linear cost into an attacker-controlled resource-exhaustion primitive. The closest concrete Agave analog is the `getVoteAccounts` RPC handler, which iterates the *entire* `bank.vote_accounts()` map — including zero-stake vote accounts that any unprivileged actor can create for the price of one rent-exempt account — and does non-trivial per-entry work (deserializing vote state, building epoch-credit history) for every single one, on every call, with no size cap.

### Title
Unbounded per-request work in `getVoteAccounts` RPC over an attacker-growable vote-account set - (File: `rpc/src/rpc.rs`)

### Summary
`RpcSolPjsonImpl::get_vote_accounts` (`rpc/src/rpc.rs:1155-1246`) iterates `bank.vote_accounts()` and, for **every** entry, deserializes the vote state, computes `last_vote`, and builds an `epoch_credits` vector before it ever applies any stake- or count-based limit. `bank.vote_accounts()` is populated by `StakesCache::check_and_store` (`runtime/src/stakes.rs:87-164`) for *any* correctly-sized, initialized vote-program account regardless of stake — confirmed by `runtime/tests/vote_account.rs:228-255` (`test_staked_nodes_zero_stake`), which explicitly shows a zero-stake vote account is inserted into `VoteAccounts`. Creating such an account only requires calling `VoteInstruction::InitializeAccount` (`programs/vote/src/vote_processor.rs:131-140`), which needs nothing but a rent-exempt vote-sized account and a self-signed `node_pubkey` — no stake, no validator identity, no special permission.

### Finding Description
`get_vote_accounts` does:
```
let vote_accounts = bank.vote_accounts();
...
vote_accounts.iter().filter_map(|(vote_pubkey, (activated_stake, account))| {
    ...
    let vote_state_view = account.vote_state_view();
    let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
    let num_epoch_credits = vote_state_view.num_epoch_credits();
    let epoch_credits = vote_state_view.epoch_credits_iter()....collect();
    Some(RpcVoteAccountInfo { ... })
})
.partition(...)
``` [1](#0-0) 

Note that the per-account work (vote-state deserialization, epoch-credit history extraction, string formatting) happens **unconditionally for every entry** in `vote_accounts`, before any stake filtering. Stake-based filtering (`activated_stake > 0`) is only applied afterwards to the *delinquent* subset via `keep_unstaked_delinquents`, and only when the caller doesn't override it — the `current`/build step itself has no cap.

`bank.vote_accounts()` is the `Stakes<StakeAccount>::vote_accounts` field, populated unconditionally whenever any account owned by the vote program passes `VoteStateVersions::is_correct_size_and_initialized`: [2](#0-1) 

This insertion path has no relationship to stake or validator identity — a zero-stake vote account is explicitly inserted and retained, as verified in the dedicated unit test: [3](#0-2) 

Creating a vote account is fully permissionless via `VoteInstruction::InitializeAccount`, requiring only a rent-exempt vote-sized account and a self-controlled signer: [4](#0-3) 

There is no analog of Sui's "1,000 dynamic field access" cap here, and no RPC-side pagination, count limit, or stake threshold gating `get_vote_accounts`'s iteration — unlike other RPC endpoints in this same file that impose explicit bounds (e.g., `MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY` bounds *within* one account's history, but not the *number of accounts* processed): [5](#0-4) 

### Impact Explanation
An attacker can create an arbitrarily large number of zero-stake vote accounts (paying only rent-exemption, which is recoverable by later withdrawing/closing the account, and a transaction fee), inflating `bank.vote_accounts()` without bound. Any subsequent single, unauthenticated `getVoteAccounts` RPC call to a node — including the default call with no filter — then forces that node to deserialize vote state and build formatted output for every spam account, with cost scaling linearly (and unbounded) with attacker-controlled input. This is a `single-client low-rate RPC crash/degradation` primitive: one caller sending one request can force disproportionate CPU/memory work on the target RPC node, degrading or crashing it for legitimate callers, entirely from an unprivileged, remotely-reachable surface.

### Likelihood Explanation
High. `VoteInstruction::InitializeAccount` is a stable, permissionless, low-privilege instruction with no rate limiting beyond normal transaction fees; nothing prevents scripting the creation of large numbers of vote accounts. `getVoteAccounts` is a standard, widely enabled public RPC method, and the vulnerable iteration path is unconditional in the current code — no feature flag or config gates it off.

### Recommendation
- Cap the number of vote accounts processed per `getVoteAccounts` call (e.g., filter to only staked/epoch-relevant accounts, or a query limit/pagination parameter), mirroring the existing per-account `epoch_credits` cap.
- Apply the stake filter *before* the expensive per-account work (state deserialization, epoch-credit extraction) rather than after building the full `RpcVoteAccountInfo`.
- Consider bounding how many zero-stake vote accounts `StakesCache`/`Stakes::vote_accounts` retains long-term, since this same unbounded map is also referenced by other bank/RPC/consensus code paths.

### Proof of Concept
1. Generate N keypairs; for each, submit a `VoteInstruction::create_account_with_config` + `InitializeAccount` transaction funding only the rent-exempt minimum for a `VoteStateV4`-sized account, with `authorized_voter`/`authorized_withdrawer` set to attacker-controlled keys and no delegated stake.
2. Repeat for a large N (e.g., hundreds of thousands), spread across multiple slots/transactions to stay within normal fee/compute limits per transaction — each account creation is independent and cheap.
3. Once these accounts populate `bank.vote_accounts()` via `StakesCache::check_and_store`, issue a single `getVoteAccounts` RPC call (no `vote_pubkey` filter) against the target node.
4. Observe RPC-node CPU/latency degradation proportional to N, since `get_vote_accounts` deserializes vote state and builds `epoch_credits` for every one of the N accounts before any stake-based filtering is applied.

### Citations

**File:** rpc/src/rpc.rs (L1181-1223)
```rust
        ) = vote_accounts
            .iter()
            .filter_map(|(vote_pubkey, (activated_stake, account))| {
                if let Some(filter_by_vote_pubkey) = filter_by_vote_pubkey
                    && *vote_pubkey != filter_by_vote_pubkey
                {
                    return None;
                }

                let vote_state_view = account.vote_state_view();
                let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
                let num_epoch_credits = vote_state_view.num_epoch_credits();
                let epoch_credits = vote_state_view
                    .epoch_credits_iter()
                    .skip(
                        num_epoch_credits
                            .saturating_sub(MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY),
                    )
                    .map(Into::into)
                    .collect();

                Some(RpcVoteAccountInfo {
                    vote_pubkey: vote_pubkey.to_string(),
                    node_pubkey: vote_state_view.node_pubkey().to_string(),
                    activated_stake: *activated_stake,
                    commission: if commission_rate_in_basis_points {
                        // Derive percent from native bps, clamping to u8::MAX.
                        let bps = vote_state_view.inflation_rewards_commission();
                        bps.div_ceil(100).min(u8::MAX as u16) as u8
                    } else {
                        vote_state_view.commission()
                    },
                    inflation_rewards_commission_bps: Some(if commission_rate_in_basis_points {
                        vote_state_view.inflation_rewards_commission()
                    } else {
                        vote_state_view.commission() as u16 * 100
                    }),
                    root_slot: vote_state_view.root_slot().unwrap_or(0),
                    epoch_credits,
                    epoch_vote_account: epoch_vote_accounts.contains_key(vote_pubkey),
                    last_vote,
                })
            })
```

**File:** runtime/src/stakes.rs (L118-127)
```rust
        if solana_vote_program::check_id(owner) {
            if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
                match VoteAccount::try_from(create_account_shared_data(account)) {
                    Ok(vote_account) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.upsert_vote_account(pubkey, vote_account)
                        };
                    }
```

**File:** runtime/tests/vote_account.rs (L238-244)
```rust
    // we call this here to initialize VoteAccounts::staked_nodes which is a OnceLock
    assert!(vote_accounts.staked_nodes().is_empty());
    let ret = vote_accounts.insert(pubkey, vote_account1.clone(), || 0);
    assert_eq!(ret, None);
    assert_eq!(vote_accounts.get_delegated_stake(&pubkey), 0);
    // ensure that we didn't add a 0 stake entry to staked_nodes
    assert_eq!(vote_accounts.staked_nodes().get(&node_pubkey), None);
```

**File:** programs/vote/src/vote_processor.rs (L130-140)
```rust
    match limited_deserialize(data, solana_packet::PACKET_DATA_SIZE as u64)? {
        VoteInstruction::InitializeAccount(vote_init) => {
            let rent =
                get_sysvar_with_account_check::rent(invoke_context, &instruction_context, 1)?;
            if !rent.is_exempt(me.get_lamports(), me.get_data().len()) {
                return Err(InstructionError::InsufficientFunds);
            }
            let clock =
                get_sysvar_with_account_check::clock(invoke_context, &instruction_context, 2)?;
            vote_state::initialize_account(&mut me, target_version, &vote_init, &signers, &clock)
        }
```

**File:** rpc-client-types/src/request.rs (L162-164)
```rust
// Limit the length of the `epoch_credits` array for each validator in a `get_vote_accounts`
// response
pub const MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY: usize = 5;
```
