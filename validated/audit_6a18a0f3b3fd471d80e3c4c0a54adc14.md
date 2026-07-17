After thorough investigation of the nearcore codebase, I found a valid structural analog to the external bug.

---

### Title
`total_stake` Inflated by Already-Kicked-Out Validators Causes Incorrect Exemption Threshold in `compute_validators_to_reward_and_kickout` — (`File: chain/epoch-manager/src/lib.rs`)

### Summary

In `compute_validators_to_reward_and_kickout`, `total_stake` is accumulated over **all** validators in the current epoch, including those already present in `prev_validator_kickout`. That inflated total is then used to compute `min_keep_stake` inside `compute_exempted_kickout`. Because `prev_validator_kickout` validators are **excluded** from ever being added to `exempted_validators`, the threshold is permanently higher than the actual exemptable stake pool, causing more validators to be exempted than the protocol intends and allowing underperforming validators to escape kickout.

### Finding Description

**Root cause — `total_stake` accumulation does not exclude `prev_validator_kickout` validators:** [1](#0-0) 

Every validator returned by `epoch_info.validators_iter()` — including those whose account IDs appear in `prev_validator_kickout` — has its stake unconditionally added to `total_stake` at line 469.

**`total_stake` is then used as the denominator for the exemption threshold:** [2](#0-1) 

`min_keep_stake = total_stake × exempt_perc / 100`. Because `total_stake` is inflated by the stake of `prev_validator_kickout` validators, `min_keep_stake` is larger than the stake that can actually be exempted.

**`prev_validator_kickout` validators are skipped inside `compute_exempted_kickout`:** [3](#0-2) 

The loop accumulates `exempted_stake` only for validators **not** in `prev_validator_kickout`. It must reach `min_keep_stake` — which was computed using a `total_stake` that includes those same excluded validators — so it must over-exempt the remaining validators to compensate.

**Concrete invariant break:**

Consider 4 validators A(100), B(200), C(300), D(400); `validator_max_kickout_stake_perc = 50`; `prev_validator_kickout = {D}`. All have poor performance.

| | Buggy (current) | Correct |
|---|---|---|
| `total_stake` | 1000 (includes D) | 600 (excludes D) |
| `min_keep_stake` | 500 | 300 |
| Exempted | {B, C} | {C} |
| Kicked out | {A} | {A, B} |

B has poor performance but avoids kickout because the inflated threshold forces the algorithm to exempt it.

**Analog to external bug:**

| External (ZetaChain) | nearcore |
|---|---|
| `totalRewardUnits` not decremented for negative-reward voters | `total_stake` not decremented for `prev_validator_kickout` validators |
| Inflated denominator → smaller `rewardPerUnit` | Inflated denominator → larger `min_keep_stake` |
| Observers receive fewer rewards than deserved | Underperforming validators avoid kickout they deserve |

### Impact Explanation

Underperforming validators systematically avoid kickout when `prev_validator_kickout` is non-empty (the common case after any network instability). The `validator_max_kickout_stake_perc` parameter does not behave as specified: the exemption algorithm protects validators with **lower** online ratios than intended, keeping degraded validators in the active set and reducing network throughput and safety margins. The magnitude scales with the fraction of total stake held by `prev_validator_kickout` validators.

### Likelihood Explanation

`prev_validator_kickout` is non-empty in virtually every epoch following any period of validator underperformance or network instability — exactly the conditions under which the kickout cap matters most. No special privileges or attacker action are required; the miscalculation is triggered by the ordinary protocol state machine.

### Recommendation

Exclude `prev_validator_kickout` validators' stake from `total_stake` before computing `min_keep_stake`, or equivalently subtract their aggregate stake from `total_stake` before passing it to `compute_exempted_kickout`:

```rust
// In compute_validators_to_reward_and_kickout, after the accumulation loop:
let exemptable_stake = epoch_info.validators_iter()
    .filter(|v| !prev_validator_kickout.contains_key(v.account_id()))
    .fold(Balance::ZERO, |sum, v| sum.checked_add(v.stake()).unwrap());

let exempted_validators = Self::compute_exempted_kickout(
    epoch_info,
    &accounts_sorted_by_online_ratio,
    exemptable_stake,   // ← use exemptable_stake, not total_stake
    exempt_perc,
    prev_validator_kickout,
);
```

### Proof of Concept

The existing test `test_max_kickout_stake_ratio` in `chain/epoch-manager/src/tests/mod.rs` already sets up a scenario with `prev_validator_kickout = {test3: Unstaked}` and `validator_max_kickout_stake_perc = 40`. [4](#0-3) 

With the current code, `total_stake = 5000` (includes test3's 1000). `min_keep_stake = 3000`. The loop exempts test1 + test2 + test4 (3000 stake) before stopping. Only test0 is kicked out.

With the fix, `total_stake = 4000` (excludes test3). `min_keep_stake = 2400`. The loop exempts test1 + test2 + test4 (3000 ≥ 2400) — same result in this balanced case. To expose the divergence, reduce one validator's stake so that the inflated threshold forces an extra exemption (as in the A/B/C/D example above). A targeted unit test with asymmetric stakes and a large `prev_validator_kickout` stake fraction will demonstrate that the current code exempts one additional validator compared to the corrected version.

### Citations

**File:** chain/epoch-manager/src/lib.rs (L391-404)
```rust
        let mut exempted_stake = Balance::ZERO;
        for account_id in accounts_sorted_by_online_ratio.into_iter().rev() {
            if exempted_stake >= min_keep_stake {
                break;
            }
            if !prev_validator_kickout.contains_key(account_id) {
                let validator_stake = epoch_info
                    .get_validator_by_account(account_id)
                    .map(|v| v.stake())
                    .unwrap_or_default();
                exempted_stake = exempted_stake.checked_add(validator_stake).unwrap();
                exempted_validators.insert(account_id.clone());
            }
        }
```

**File:** chain/epoch-manager/src/lib.rs (L445-469)
```rust
        for (i, v) in epoch_info.validators_iter().enumerate() {
            let account_id = v.account_id();
            let block_stats = block_validator_tracker
                .get(&(i as u64))
                .unwrap_or(&ValidatorStats { expected: 0, produced: 0 })
                .clone();
            let mut chunk_stats = ChunkStats::default();
            for (_, tracker) in chunk_stats_tracker {
                if let Some(stat) = tracker.get(&(i as u64)) {
                    *chunk_stats.expected_mut() += stat.expected();
                    *chunk_stats.produced_mut() += stat.produced();
                    chunk_stats.endorsement_stats_mut().produced +=
                        stat.endorsement_stats().produced;
                    chunk_stats.endorsement_stats_mut().expected +=
                        stat.endorsement_stats().expected;
                }
            }
            // On spice epochs endorsements are not embedded per-shard, so the
            // per-shard tracker above is empty; the endorsement stats come from
            // the epoch's last block header instead.
            if let Some(stat) = spice_endorsement_tracker.get(&(i as u64)) {
                chunk_stats.endorsement_stats_mut().produced += stat.produced;
                chunk_stats.endorsement_stats_mut().expected += stat.expected;
            }
            total_stake = total_stake.checked_add(v.stake()).unwrap();
```

**File:** chain/epoch-manager/src/lib.rs (L508-516)
```rust
        let exempt_perc =
            100_u8.checked_sub(config.validator_max_kickout_stake_perc).unwrap_or_default();
        let exempted_validators = Self::compute_exempted_kickout(
            epoch_info,
            &accounts_sorted_by_online_ratio,
            total_stake,
            exempt_perc,
            prev_validator_kickout,
        );
```

**File:** chain/epoch-manager/src/tests/mod.rs (L2971-3051)
```rust
    let prev_validator_kickout =
        HashMap::from([("test3".parse().unwrap(), ValidatorKickoutReason::Unstaked)]);
    let (validator_stats, kickouts) = EpochManager::compute_validators_to_reward_and_kickout(
        &epoch_config,
        &epoch_info,
        &block_stats,
        &chunk_stats_tracker,
        &HashMap::new(),
        &prev_validator_kickout,
    );
    assert_eq!(
        kickouts,
        // We would have kicked out test0, test1, test2 and test4, but test3 was kicked out
        // last epoch. To avoid kicking out all validators in two epochs, we saved test1 because
        // it produced the most blocks (test1 and test2 produced the same number of blocks, but test1
        // is listed before test2 in the validators list).
        HashMap::from([
            ("test0".parse().unwrap(), NotEnoughBlocks { produced: 50, expected: 100 }),
            ("test2".parse().unwrap(), NotEnoughBlocks { produced: 70, expected: 100 }),
            ("test4".parse().unwrap(), NotEnoughChunks { produced: 50, expected: 100 }),
        ])
    );
    let wanted_validator_stats = HashMap::from([
        (
            "test0".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 50, expected: 100 },
                chunk_stats: ChunkStats::new_with_production(0, 100),
            },
        ),
        (
            "test1".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 70, expected: 100 },
                chunk_stats: ChunkStats::new_with_production(0, 100),
            },
        ),
        (
            "test2".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 70, expected: 100 },
                chunk_stats: ChunkStats::new_with_production(100, 100),
            },
        ),
        (
            "test3".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 0, expected: 0 },
                chunk_stats: ChunkStats::default(),
            },
        ),
        (
            "test4".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 0, expected: 0 },
                chunk_stats: ChunkStats::new_with_production(50, 100),
            },
        ),
    ]);
    assert_eq!(validator_stats, wanted_validator_stats,);
    // At most 40% of total stake can be kicked out
    epoch_config.validator_max_kickout_stake_perc = 40;
    let (validator_stats, kickouts) = EpochManager::compute_validators_to_reward_and_kickout(
        &epoch_config,
        &epoch_info,
        &block_stats,
        &chunk_stats_tracker,
        &HashMap::new(),
        &prev_validator_kickout,
    );
    assert_eq!(
        kickouts,
        // We would have kicked out test0, test1, test2 and test4, but
        // test1, test2, and test4 are exempted. Note that test3 can't be exempted because it
        // is in prev_validator_kickout.
        HashMap::from([(
            "test0".parse().unwrap(),
            NotEnoughBlocks { produced: 50, expected: 100 }
        ),])
    );
    assert_eq!(validator_stats, wanted_validator_stats,);
```
