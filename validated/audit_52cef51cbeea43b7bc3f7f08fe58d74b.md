Audit Report

## Title
Unbounded per-request work in `getVoteAccounts` RPC over an attacker-growable vote-account set - (File: `rpc/src/rpc.rs`)

## Summary
`get_vote_accounts` (`rpc/src/rpc.rs:1155-1246`) unconditionally iterates the full `bank.vote_accounts()` map and, for every entry, deserializes the vote state and builds an `epoch_credits` vector before any stake-based filtering occurs. Because `StakesCache::check_and_store` (`runtime/src/stakes.rs:87-164`) inserts any correctly-sized, initialized vote-program account into this map regardless of stake, an unprivileged attacker can permissionlessly grow this set by creating many zero-stake vote accounts, forcing disproportionate CPU work on any node handling a single `getVoteAccounts` call.

## Finding Description
The core loop at `rpc/src/rpc.rs:1181-1224` does:
```rust
vote_accounts
    .iter()
    .filter_map(|(vote_pubkey, (activated_stake, account))| {
        ...
        let vote_state_view = account.vote_state_view();
        let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
        let num_epoch_credits = vote_state_view.num_epoch_credits();
        let epoch_credits = vote_state_view.epoch_credits_iter()...collect();
        Some(RpcVoteAccountInfo { ... })
    })
    .partition(...)
``` [1](#0-0) 

This per-entry work (vote-state deserialization, epoch-credit extraction, string formatting of `vote_pubkey`/`node_pubkey`) executes for every account in `vote_accounts` before any activated-stake filtering. The only stake-based filter (`activated_stake > 0`) is applied afterward, and only to the *delinquent* partition, and only when `keep_unstaked_delinquents` is false: [2](#0-1) . There is no size cap, pagination, or upfront stake threshold gating the initial iteration/collection over `vote_accounts`.

`bank.vote_accounts()` is backed by `StakesCache`, whose `check_and_store` inserts any account owned by the vote program into the cache as long as it is correctly sized and initialized — with no stake requirement:
```rust
if solana_vote_program::check_id(owner) {
    if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
        match VoteAccount::try_from(create_account_shared_data(account)) {
            Ok(vote_account) => {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_vote_account(pubkey, vote_account)
                };
            }
            ...
``` [3](#0-2) 

This confirms zero-stake vote accounts are unconditionally added to the map that `get_vote_accounts` iterates in full, and creating such an account only requires the permissionless `VoteInstruction::InitializeAccount` (rent-exempt account + self-controlled signer, no delegated stake, no special permission).

## Impact Explanation
This matches the allowed impact category of "single-client low-rate RPC crash/degradation": an unprivileged, remote, unauthenticated caller can force one RPC node to do unbounded, attacker-scaled CPU/memory work (vote-state deserialization and epoch-credit extraction for every attacker-created zero-stake vote account) via a single `getVoteAccounts` call, with no cap on the number of accounts processed, degrading the node for legitimate callers.

## Likelihood Explanation
High. Creating zero-stake vote accounts via `VoteInstruction::InitializeAccount` only costs rent-exemption (recoverable) and normal transaction fees, with no rate limiting specific to vote-account creation. `getVoteAccounts` is a standard, widely enabled public RPC method, and the vulnerable full-iteration-before-filter code path is unconditional in the current code, confirmed directly by reading `rpc/src/rpc.rs:1181-1240` and `runtime/src/stakes.rs:118-127`.

## Recommendation
- Apply stake-based (or other bounding) filters to `vote_accounts` before doing the expensive per-account work (vote-state deserialization, epoch-credit extraction), not just after building `RpcVoteAccountInfo` for the delinquent subset.
- Add an explicit cap/pagination on the number of vote accounts processed per `getVoteAccounts` call.
- Consider bounding how many zero-stake vote accounts `StakesCache`/`Stakes::vote_accounts` retains, since other bank/RPC/consensus paths reference this same unbounded map.

## Proof of Concept
1. Generate N keypairs; for each, submit `VoteInstruction::create_account_with_config` + `InitializeAccount` funding only the rent-exempt minimum, with attacker-controlled `authorized_voter`/`authorized_withdrawer` and no delegated stake.
2. Repeat for large N, spread across multiple transactions/slots (each creation is independent and cheap, gated only by normal fees).
3. Once these zero-stake accounts populate `bank.vote_accounts()` via `StakesCache::check_and_store`, issue a single unauthenticated `getVoteAccounts` RPC call (no `vote_pubkey` filter) against the target node.
4. Observe RPC-node CPU/latency degradation scaling with N, since `get_vote_accounts` deserializes vote state and builds `epoch_credits` for every one of the N accounts before any stake-based filtering is applied.

### Citations

**File:** rpc/src/rpc.rs (L1181-1224)
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
            .partition(|vote_account_info| {
```

**File:** rpc/src/rpc.rs (L1232-1240)
```rust
        let keep_unstaked_delinquents = config.keep_unstaked_delinquents.unwrap_or_default();
        let delinquent_vote_accounts = if !keep_unstaked_delinquents {
            delinquent_vote_accounts
                .into_iter()
                .filter(|vote_account_info| vote_account_info.activated_stake > 0)
                .collect::<Vec<_>>()
        } else {
            delinquent_vote_accounts
        };
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
