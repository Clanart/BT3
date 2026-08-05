### Title
Unhandled panic in `stake_weighted_slot_leaders()` when no vote account has non-zero stake bricks leader schedule computation - (`leader-schedule/src/lib.rs`)

### Summary
`LeaderSchedule::new()` filters the incoming `VoteAccountsHashMap` to keep only vote accounts with `stake > 0` and passes the resulting `(SlotLeader, stake)` vector to `stake_weighted_slot_leaders()`, which unconditionally calls `WeightedU64Index::new(stakes).unwrap()`. If the filtered vector is empty (or the total weight is zero), `WeightedU64Index::new()` deterministically returns `Err(Error::InvalidInput)` / `Err(Error::InsufficientNonZero)`, and the `.unwrap()` panics. This mirrors the Notional Exponent bug class: code assumes a quantity (there, the exit balance of a token; here, aggregate non-zero stake) can never be zero, and a `require`/`unwrap` on that assumption turns an edge case into an unconditional revert of a function that every participant depends on for a critical operation.

### Finding Description
`stake_weighted_slot_leaders()` documents the exact fragility itself: [1](#0-0) 

```
// Note: passing in zero keyed stakes will cause a panic.
fn stake_weighted_slot_leaders(...) -> Vec<SlotLeader> {
    ...
    let weighted_index = WeightedU64Index::new(stakes).unwrap();
    ...
}
```

`WeightedU64Index::new()` is a `Result`-returning function that explicitly guards against both the empty-input and the all-zero-weight cases: [2](#0-1) 

```
pub fn new(mut weights: Vec<u64>) -> Result<Self, Error> {
    ...
    if weights.pop().is_none() {
        return Err(Error::InvalidInput);
    }
    let Some(total_weight) = NonZero::new(total_weight) else {
        return Err(Error::InsufficientNonZero);
    };
    ...
}
```

The caller, `LeaderSchedule::new()`, is the only place feeding `stake_weighted_slot_leaders()` in production, and it filters out all zero-stake vote accounts before calling it: [3](#0-2) 

```
// Note: passing in zero vote accounts will cause a panic.
pub fn new(
    vote_accounts_map: &VoteAccountsHashMap,
    epoch: Epoch,
    len: usize,
    repeat: NonZeroUsize,
) -> Self {
    let slot_leader_stakes: Vec<_> = vote_accounts_map
        .iter()
        .filter(|(_pubkey, (stake, _account))| *stake > 0)
        ...
    let slot_leaders = stake_weighted_slot_leaders(slot_leader_stakes, epoch, len, repeat);
    ...
}
```

Both comments explicitly acknowledge the invariant ("zero keyed stakes will cause a panic", "zero vote accounts will cause a panic"), yet neither `LeaderSchedule::new()` nor `stake_weighted_slot_leaders()` validate the invariant before calling the fallible constructor — the error is discarded via `.unwrap()` instead of being propagated or defended against, exactly like the `require(hasRequest)` pattern in the Solidity report that assumes a condition can never be false.

`LeaderSchedule::new()` is invoked from the runtime's leader-schedule computation path (`runtime/src/leader_schedule_utils.rs`), which every validator runs deterministically from `EpochStakes`/`VoteAccountsHashMap` derived from bank state, and is consumed by `ledger/src/leader_schedule_cache.rs`, `core/src/replay_stage.rs`, `turbine/src/broadcast_stage.rs`, and `poh/src/poh_recorder.rs` for leader determination, block production/broadcast targeting, and replay. Because the function is deterministic given bank state, if the set of vote accounts with non-zero stake for an epoch ever becomes empty, every honest validator computing that epoch's schedule will panic at the same point, independent of any peer behaving maliciously.

### Impact Explanation
If the invariant "at least one vote account has non-zero stake" is ever violated for a given epoch (e.g., during bring-up of new/private clusters, extreme stake attrition/deactivation scenarios, or any bank state where `EpochStakes` legitimately yields all-zero or empty stake), the panic occurs inside code that is on the critical path for leader schedule generation, replay, and block production across the fleet — a deterministic, network-wide crash rather than an isolated node fault. This matches the report's core impact class: a supposedly "can't happen" zero condition turning a value/derivation function into an unconditional revert that halts operation for everyone depending on it, rather than degrading gracefully.

### Likelihood Explanation
Likelihood is low for mainnet-beta specifically, since genesis/bootstrap stake and normal warm-up/cooldown mechanics make an epoch with zero total effective stake practically implausible under current stake-program rules. However, the same code path is generic and used for any cluster (test validators, private/permissioned networks, or future stake-configuration changes), and the source code itself contains two independent comments flagging this exact failure mode without any actual guard against it — indicating the developers were aware of but did not close this gap, precisely mirroring the "incorrect assumption that a value will never be zero" root cause in the referenced report.

### Recommendation
Replace the `.unwrap()` in `stake_weighted_slot_leaders()` (`leader-schedule/src/lib.rs`) with explicit handling of `WeightedU64Index::new()`'s `Result`, and have `LeaderSchedule::new()` validate that `slot_leader_stakes` is non-empty (and total stake non-zero) before calling it — returning an empty/no-op schedule or a well-defined error instead of panicking, consistent with the mitigation direction for the analogous `require(hasRequest)` issue (handle the zero case instead of asserting it away).

### Proof of Concept
`random/src/weighted.rs` test already demonstrates the underlying failure condition: [4](#0-3) 

```
fn test_weighted_u64_index_error_on_new() {
    assert_matches!(WeightedU64Index::new(vec![]), Err(Error::InvalidInput));
    assert_matches!(
        WeightedU64Index::new(vec![0, 0, 0, 0, 0]),
        Err(Error::InsufficientNonZero)
    );
    ...
}
```

and `leader-schedule/src/lib.rs` has a corresponding `#[should_panic]` test proving the unguarded `.unwrap()` panics when it is fed such input: [5](#0-4) 

```
#[test]
#[should_panic]
fn test_zero_stake_panics() {
    let _ = stake_weighted_slot_leaders(
        vec![(SlotLeader::new_unique(), 0), (SlotLeader::new_unique(), 0)],
        0,
        5,
        NZ_1,
    );
}
```

`LeaderSchedule::new()` filters zero-stake entries before this call, so any bank state yielding an empty (or all-zero) `VoteAccountsHashMap` for an epoch reaches this exact panic path in production.

### Citations

**File:** leader-schedule/src/lib.rs (L43-57)
```rust
// Note: passing in zero keyed stakes will cause a panic.
fn stake_weighted_slot_leaders(
    mut slot_leader_stakes: Vec<(SlotLeader, u64)>,
    epoch: Epoch,
    len: usize,
    repeat: NonZeroUsize,
) -> Vec<SlotLeader> {
    let repeat = repeat.get();
    debug_assert!(
        len.is_multiple_of(repeat),
        "expected `len` {len} to be divisible by `repeat` {repeat}"
    );
    sort_stakes(&mut slot_leader_stakes);
    let (slot_leaders, stakes): (Vec<_>, Vec<_>) = slot_leader_stakes.into_iter().unzip();
    let weighted_index = WeightedU64Index::new(stakes).unwrap();
```

**File:** leader-schedule/src/lib.rs (L263-272)
```rust
    #[test]
    #[should_panic]
    fn test_zero_stake_panics() {
        let _ = stake_weighted_slot_leaders(
            vec![(SlotLeader::new_unique(), 0), (SlotLeader::new_unique(), 0)],
            0,
            5,
            NZ_1,
        );
    }
```

**File:** random/src/weighted.rs (L25-38)
```rust
    pub fn new(mut weights: Vec<u64>) -> Result<Self, Error> {
        // Calculate prefix sum of weights such that binary search can find the index of the
        // chosen weight.
        let mut total_weight = 0u64;
        for weight in weights.iter_mut() {
            total_weight = total_weight.checked_add(*weight).ok_or(Error::Overflow)?;
            *weight = total_weight;
        }
        if weights.pop().is_none() {
            return Err(Error::InvalidInput);
        }
        let Some(total_weight) = NonZero::new(total_weight) else {
            return Err(Error::InsufficientNonZero);
        };
```

**File:** random/src/weighted.rs (L96-102)
```rust
    #[test]
    fn test_weighted_u64_index_error_on_new() {
        assert_matches!(WeightedU64Index::new(vec![]), Err(Error::InvalidInput));
        assert_matches!(
            WeightedU64Index::new(vec![0, 0, 0, 0, 0]),
            Err(Error::InsufficientNonZero)
        );
```

**File:** leader-schedule/src/vote_keyed.rs (L27-48)
```rust
impl LeaderSchedule {
    // Note: passing in zero vote accounts will cause a panic.
    pub fn new(
        vote_accounts_map: &VoteAccountsHashMap,
        epoch: Epoch,
        len: usize,
        repeat: NonZeroUsize,
    ) -> Self {
        let slot_leader_stakes: Vec<_> = vote_accounts_map
            .iter()
            .filter(|(_pubkey, (stake, _account))| *stake > 0)
            .map(|(&vote_address, (stake, vote_account))| {
                (
                    SlotLeader {
                        vote_address,
                        id: *vote_account.node_pubkey(),
                    },
                    *stake,
                )
            })
            .collect();
        let slot_leaders = stake_weighted_slot_leaders(slot_leader_stakes, epoch, len, repeat);
```
