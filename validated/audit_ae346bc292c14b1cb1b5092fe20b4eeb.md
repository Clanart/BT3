### Title
Stale `StakesCache` entries when a stake/vote account's owner changes without zeroing lamports let a validator's voting/staking power persist after reassignment - ([File: runtime/src/stakes.rs])

### Summary
The external report's broken invariant is: a system that keys trust/behavior purely off an *address* implicitly assumes the account/contract at that address retains a single, stable identity/type, so it never re-validates identity when the underlying content changes. In Solidity that gap is exploited via `CREATE2` redeployment; the equivalent gap in Agave is the `StakesCache`, which keys cached vote/stake state by `Pubkey` and only evicts an entry when it observes `lamports() == 0`, but explicitly does **not** re-check ownership on every update when lamports remain non-zero.

### Finding Description
`StakesCache::check_and_store` is the single place the runtime bank keeps its in-memory `Stakes<StakeAccount>` cache (vote accounts and stake delegations, used to compute leader schedule, vote/stake weight, and rewards) synchronized with the accounts state as transactions are processed: [1](#0-0) 

The zero-lamport branch removes an entry from the cache only when the account balance drops to zero: [2](#0-1) 

But an account can be *reassigned away from* the vote/stake program (i.e., have its `owner` field changed to, say, the System Program) via `SystemInstruction::Assign`/`Allocate`/`Transfer`‑with‑reassignment style operations, or have its owner changed by any program with write access, **without** its lamport balance going to zero. The code's own comment documents this gap and marks it as an unresolved `TODO`: [3](#0-2) 

Because the non‑zero-lamport branches only match on `solana_vote_program::check_id(owner)` / `stake_program::check_id(owner)`, if an account's owner is changed to something else while lamports remain non‑zero, `check_and_store` falls through both branches, does nothing, and the stale `VoteAccount`/`StakeAccount` entry keyed by that `pubkey` is left in `Stakes` untouched from its last valid appearance.

`Bank` relies on this cache (`vote_accounts()`, `stake_delegations()`, delegated_stakes) to compute the leader schedule, vote credits, and stake-weighted consensus decisions — i.e., authoritative state that is *not* re-derived from a full account scan every time. This mirrors exactly the report's invariant break: the code treats "same pubkey" as "same semantic entity" and fails to re-validate the entity's current owner/type on every mutating path, only on the lamports=0 path.

### Impact Explanation
If exploitable, this allows an actor who controls a stake or vote account (its own vote/stake account, or one for which they hold authority) to reassign the account to another owner without fully draining lamports, leaving Bank's in-memory `Stakes` cache with a stale vote-account or stake-delegation entry that no longer corresponds to a real vote/stake account on-chain. Because `Stakes` feeds leader-schedule computation, stake-weighted vote counting, and rewards distribution, a stale/incorrect entry could cause the bank to compute leader schedules or consensus-relevant stake weights from data that no longer reflects the actual state of accounts, i.e., false acceptance of stake/vote weight that should not exist. This falls into the "false execution/rooting/acceptance" / consensus-integrity category named in the task's impact list.

### Likelihood Explanation
The likelihood is **uncertain** and could not be fully confirmed with the available tooling/iterations. The code comment shows the developers are aware of the gap and left a `TODO`, which suggests either (a) it is a real, currently-open latent bug, or (b) some other layer (e.g., a full-account-scan reconciliation each epoch boundary, or a guarantee elsewhere that owner never changes without lamports going to zero for these account types) mitigates it, which I could not fully verify given remaining iterations (I was unable to inspect all `check_and_store` call sites in `runtime/src/bank.rs` to confirm whether every account-owner-change path funnels through this function, or whether periodic full cache rebuilds exist that would bound the staleness window).

### Recommendation
In `StakesCache::check_and_store`, before returning early, explicitly check whether `pubkey` is currently present as a vote account or stake delegation in the cache and whether the *new* `owner` no longer matches the expected program id; if so, evict the stale entry (mirroring the zero-lamport eviction paths) regardless of lamport balance. This closes the gap referenced by the existing `TODO` comment and referenced upstream PR discussion.

### Proof of Concept
Conceptual reproduction (not verified end-to-end due to tool/iteration limits):
1. Create a stake or vote account with non-zero lamports so it is picked up by `StakesCache::check_and_store` and cached.
2. Submit a transaction that changes the account's `owner` field to a non-stake/vote program (e.g., via the System Program's `Assign` instruction, if permitted for the account, or via any custom program with write access to reassign ownership) while keeping lamports non-zero.
3. Because `account.lamports() != 0` and the new `owner` matches neither `solana_vote_program::check_id` nor `stake_program::check_id`, `check_and_store` takes no action.
4. Query `Bank::vote_accounts()`/`stake_delegations()` (or the leader schedule/stake-weight consumers built from `Stakes`) and observe the stale entry for `pubkey` persists even though the account is no longer a vote/stake account on-chain.

Given I could not fully trace every call path of `check_and_store` and confirm the absence of compensating controls, this should be validated further before being treated as a confirmed exploitable vulnerability rather than a documented-but-unconfirmed gap.

### Citations

**File:** runtime/src/stakes.rs (L87-116)
```rust
    pub(crate) fn check_and_store(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
        let owner = account.owner();
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
```
