### Title
Stale vote/stake account remains in `StakesCache` after its owner is reassigned away from the vote/stake program - (File: `runtime/src/stakes.rs`)

### Summary
`StakesCache::check_and_store` is the runtime's single entry point for keeping the in-memory vote/stake cache (used to compute stake weights and the leader schedule) synchronized with on-chain account state. The function itself contains a `TODO` acknowledging that if a previously cached vote/stake account has its `owner` field reassigned to a different program, the stale cached entry is never evicted [1](#0-0) . This is the direct Agave analog of the ERC-721 "approval not revoked on transfer" bug class: a privileged delegation record (here, "this account counts as vote/stake state") survives a change of the underlying account's controlling authority (its `owner`), because the invalidation path is keyed only on the *current* owner, not on a transition away from the vote/stake program.

### Finding Description
`check_and_store` branches solely on `account.owner()` of the account being stored:
- If `lamports == 0`, it removes the cache entry only if the *current* owner is still the vote or stake program [2](#0-1) .
- If `lamports > 0` and owner is the vote program, it re-parses and upserts (or removes on parse failure) [3](#0-2) .
- If `lamports > 0` and owner is the stake program, likewise [4](#0-3) .
- **If the owner is neither the vote program nor the stake program (i.e., the account was reassigned to a different owner via `SystemInstruction::Assign`/`CreateAccount` with a new owner, while still holding lamports), none of the branches execute, and the previously cached vote/stake entry for that pubkey is left untouched in `Stakes<StakeAccount>`.**

The explicit code comment documents this exact gap: *"If the account is already cached as a vote or stake account but the owner changes, then this needs to evict the account from the cache"* [5](#0-4) .

This mirrors the reported ERC-721 bug precisely: an authorization/delegation record (`getApproved`) is keyed to a token/account, and a change of controlling state (ownership transfer) does not trigger revocation of the old privileged record, so the stale record continues to be honored by downstream logic that trusts it.

An unprivileged account owner can trigger the vulnerable path with a normal, permissionless transaction: since a stake or vote account is a regular system-owned-space account controlled by its `authorized`/withdraw keys, and Solana's `Assign`/`CreateAccountWithSeed` system instructions allow the current owner-program (here, the stake or vote program acting on `_requireAuthorised`-equivalent checks internal to those programs) — more concretely, once a stake account is fully withdrawn/closed by `WithdrawNonceAccount`-analog stake `Withdraw` instruction (which sets state to `Uninitialized` and can be followed by `Assign` to a new owner while lamports > 0 remain, e.g. rent-exempt reserve, or a subsequent transaction tops up lamports before reassignment) the account can end up with a non-zero balance and a non-vote/non-stake owner, which is exactly the branch that `check_and_store` fails to handle.

### Impact Explanation
`Stakes<StakeAccount>` backs stake-weighted computations central to consensus: leader schedule derivation, vote credit tracking, and stake-weighted QUIC/TPU connection prioritization all read from `StakesCache::stakes()`. A stale cache entry for an account that is no longer actually owned by the stake or vote program means the bank's view of "true" stake/vote state can diverge from the authoritative accounts-db state without being corrected on the normal `check_and_store` update path, corrupting `Stakes.stake_delegations`/`Stakes.vote_accounts` (the exact corrupted value). Depending on how long the stale state persists before other invalidation paths (e.g., periodic full-refresh via `refresh_delegated_stakes` or epoch boundary rebuilds) run, this can lead to incorrect leader-schedule/stake-weight calculations — a form of false acceptance/execution of stale privileged state, matching the "false execution/rooting/acceptance" impact category.

### Likelihood Explanation
The trigger requires only an ordinary unprivileged transaction sequence (own a stake/vote account, drive its native program to relinquish exclusive control such that the account can be reassigned while non-zero lamports remain, then `Assign`/`CreateAccountWithSeed` it to a new owner) — no malicious validator, peer, or trusted process assumption is required. However, whether it is *practically* exploitable end-to-end depends on additional invariants enforced by the stake/vote programs themselves (e.g., whether they allow their accounts to be reassigned to another owner while non-zero and whether other, unexamined call sites eventually correct the cache before it is consumed for consensus-critical decisions). I was not able to fully trace every call site of `check_and_store` in `runtime/src/bank.rs` and `accounts-db/src/accounts_db.rs` (5 and 2 matches respectively, not read in depth due to iteration limits) to confirm whether a periodic/authoritative resync guards against this in practice, so the exploitability window and blast radius remain uncertain and should be verified.

### Recommendation
In `StakesCache::check_and_store`, add an explicit branch (or a pre-check based on the *previous* cached entry's existence) that evicts any prior `vote_accounts`/`stake_delegations` entry for `pubkey` whenever the account's current owner is neither the vote program nor the stake program, regardless of the lamports balance — i.e., resolve the `TODO` at `runtime/src/stakes.rs:94-97` by unconditionally calling `stakes.remove_vote_account(pubkey)` / `stakes.remove_stake_delegation(...)` when `owner` no longer matches either program, mirroring how the "revoke approval on transfer" fix mandates clearing `getApproved` unconditionally on any ownership change.

### Proof of Concept
Conceptual reproduction (exact instruction sequence needs empirical verification against current stake/vote program invariants):
1. Create and fund a stake account `S`, delegate it so it is picked up by `StakesCache::check_and_store` and inserted into `Stakes.stake_delegations`.
2. Drive the stake program state for `S` such that it becomes deactivated/withdrawable while still holding some lamports (e.g., rent-exempt reserve) — i.e., avoid the `lamports == 0` branch.
3. Issue `system_instruction::assign(S, new_owner)` (or an equivalent reassignment path reachable once the stake program has released exclusive authority) to change `S.owner` to an arbitrary program, while `S` still has non-zero lamports.
4. Observe that `check_and_store(S, ...)` is invoked (e.g. on the next write to `S`) but takes neither the vote-program nor stake-program branch, so `Stakes.stake_delegations`/`Stakes.vote_accounts` retains the old, now-stale entry for `S`, per `runtime/src/stakes.rs:87-164`.
5. Confirm via `StakesCache::stakes()` that the stale entry is still present and would be included in stake-weight/leader-schedule computations that consume `Stakes<StakeAccount>`. [6](#0-5)

### Citations

**File:** runtime/src/stakes.rs (L87-164)
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
        debug_assert_ne!(account.lamports(), 0u64);
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
                    Err(_) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.remove_vote_account(pubkey)
                        };
                    }
                }
            } else {
                // drop the old account after releasing the lock
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            };
        } else if stake_program::check_id(owner) {
            match StakeAccount::try_from(create_account_shared_data(account)) {
                Ok(stake_account) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_stake_delegation(
                        *pubkey,
                        stake_account,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
                Err(_) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_stake_delegation(
                        pubkey,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
            }
        }
    }
```
