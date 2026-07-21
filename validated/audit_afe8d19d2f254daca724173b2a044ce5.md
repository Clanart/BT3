### Title
Bootstrap-Window `fee_proposal_fri` Bounds Bypass Corrupts `fee_actual` Floor and Permanently Inflates L2 Gas Price — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

During the first `fee_proposal_window_size` (10) blocks after Starknet V0_14_3 activation, the validator's `fee_proposal_fri` bounds check is unconditionally skipped because `fee_actual` is `None`. A malicious proposer who controls even a fraction of those bootstrap slots can inject an arbitrarily large `fee_proposal_fri` value. Once the window fills, `compute_fee_actual` returns the median of those injected values as the authoritative `fee_actual`, which `calculate_next_l2_gas_price_for_fin` then uses as the hard floor for the EIP-1559 gas price. The resulting `next_l2_gas_price` is committed to `BlockHeaderWithoutHash` in state sync storage and propagated to every subsequent block, making all transactions whose `max_price_per_unit` falls below the inflated floor fail fee checks — a network-wide economic freeze.

---

### Finding Description

**Root cause — missing bounds check in the bootstrap window**

In `validate_proposal.rs`, the fee-proposal bounds check is guarded by an `if let` that requires both `fee_actual` and `fee_proposal` to be `Some`:

```rust
// Validate fee_proposal is within the configured margin of fee_actual.
// During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    let (lower_bound, upper_bound) = fee_proposal_bounds(
        fee_actual,
        VersionedConstants::latest_constants().fee_proposal_margin_ppt,
    );
    if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
        return Err(ValidateProposalError::InvalidProposalInit(...));
    }
}
``` [1](#0-0) 

`fee_actual` is `None` whenever `height < window_size` (currently 10), because `compute_fee_actual` returns `None` when the window is incomplete:

```rust
let Some(start) = height.0.checked_sub(window_size) else {
    warn!("Cannot compute fee_actual for height {height}: height is below window_size ({window_size})");
    return None;
};
``` [2](#0-1) 

During those 10 blocks, any `fee_proposal_fri` value — including `u128::MAX` — passes validation without any bounds check.

**Propagation path — injected value becomes the gas-price floor**

After consensus, the accepted `fee_proposal_fri` is stored in `BlockHeaderWithoutHash.fee_proposal_fri` and committed to state sync:

```rust
fee_proposal_fri: init.fee_proposal_fri,
``` [3](#0-2) 

On the next node start, `initialize_fee_proposals_window` reads these values back from state sync into `fee_proposals_window`. Once height reaches `window_size`, `compute_fee_actual` returns the median of the injected values as `fee_actual`.

`calculate_next_l2_gas_price_for_fin` then uses `fee_actual` as a hard floor:

```rust
let effective_min = match fee_actual {
    Some(fa) => GasPrice(max(config_min.0, fa.0)),
    None => config_min,
};
calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
``` [4](#0-3) 

The result is stored as `next_l2_gas_price` in every subsequent block header and used for all transaction fee checks. The EIP-1559 mechanism can only reduce the price by `1/gas_price_max_change_denominator` (1/48) per block, so recovery from an extreme floor takes thousands of blocks.

**Commitment binding does not prevent the attack**

`proposal_commitment_from` binds `fee_proposal_fri` into the proposal commitment via Poseidon hash:

```rust
ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
``` [5](#0-4) 

This prevents equivocation on `fee_proposal_fri` after consensus, but it does **not** prevent an extreme value from being accepted in the first place — the commitment merely binds whatever value the proposer chose, including an extreme one.

---

### Impact Explanation

An extreme `fee_actual` floor (e.g., `u128::MAX` or even 100× the normal value) causes `calculate_next_base_gas_price` to return an extreme `next_l2_gas_price`. This value is written into `BlockHeaderWithoutHash.next_l2_gas_price` and propagated to the blockifier as the block's L2 gas price. Every transaction whose `max_price_per_unit` is below this floor fails the `check_fee_bounds` pre-validation check, effectively freezing all user activity. This is a **Critical** incorrect fee/gas effect with direct economic impact. [6](#0-5) 

---

### Likelihood Explanation

The attack window is the first 10 blocks after V0_14_3 activation. In a small or newly bootstrapped committee, a single malicious validator may be selected as proposer multiple times within those 10 slots. Injecting extreme values into the majority of the 10 bootstrap slots shifts the median `fee_actual` to an extreme value. Even a minority injection (e.g., 5 of 10 slots) is sufficient to dominate the median. The trigger requires only normal proposer rotation — no special privilege beyond being a committee member.

---

### Recommendation

Apply the same bounds check unconditionally during the bootstrap window. When `fee_actual` is `None`, use a protocol-defined absolute cap (e.g., `max_l2_gas_price` from versioned constants) instead of skipping the check entirely:

```rust
let effective_fee_actual = proposal_init_validation.fee_actual
    .unwrap_or(MAX_BOOTSTRAP_FEE_PROPOSAL);  // hard cap, not unbounded

if let Some(fee_proposal) = init_proposed.fee_proposal_fri {
    let (lower_bound, upper_bound) = fee_proposal_bounds(
        effective_fee_actual,
        VersionedConstants::latest_constants().fee_proposal_margin_ppt,
    );
    if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
        return Err(...);
    }
}
```

Alternatively, enforce an absolute maximum on `fee_proposal_fri` regardless of `fee_actual` availability, so no single bootstrap proposal can inject a value that would make the network unusable.

---

### Proof of Concept

1. Network activates Starknet V0_14_3. `fee_proposals_window` is empty; `fee_actual` is `None` for heights 0–9.
2. A malicious validator is selected as proposer for blocks 0–4 (5 of the 10 bootstrap slots).
3. For each of those 5 blocks, the proposer sets `fee_proposal_fri = u128::MAX` in `ProposalInit`.
4. `is_proposal_init_valid` reaches the bounds check: `if let (Some(fee_actual), Some(fee_proposal)) = (None, Some(u128::MAX))` — the pattern does **not** match, so the check is skipped entirely. The proposal is accepted.
5. The 5 extreme values are stored in `BlockHeaderWithoutHash.fee_proposal_fri` and committed to state sync.
6. At height 10, `compute_fee_actual` computes the median of `[u128::MAX, u128::MAX, u128::MAX, u128::MAX, u128::MAX, normal, normal, normal, normal, normal]` = `u128::MAX` (the 5th element when sorted).
7. `calculate_next_l2_gas_price_for_fin` sets `effective_min = u128::MAX`.
8. `calculate_next_base_gas_price` returns `u128::MAX` as `next_l2_gas_price`.
9. All subsequent transactions fail `check_fee_bounds` because their `max_price_per_unit < u128::MAX`. The network is frozen. [1](#0-0) [7](#0-6) [6](#0-5) [8](#0-7)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L396-416)
```rust
    // Validate fee_proposal is within the configured margin of fee_actual.
    // During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
    if let (Some(fee_actual), Some(fee_proposal)) =
        (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
    {
        let (lower_bound, upper_bound) = fee_proposal_bounds(
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "Fee proposal out of bounds: fee_actual={}, fee_proposal={}, allowed \
                     range=[{lower_bound}, {upper_bound}]",
                    fee_actual.0, fee_proposal.0
                ),
            ));
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-92)
```rust
pub fn compute_fee_actual(
    fee_proposals_window: &BTreeMap<BlockNumber, Option<GasPrice>>,
    height: BlockNumber,
    window_size: u64,
) -> Option<GasPrice> {
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
    let window_size_usize = usize::try_from(window_size).expect("window_size fits in usize");
    let mut window = Vec::with_capacity(window_size_usize);
    for source_height in (start..height.0).map(BlockNumber) {
        match fee_proposals_window.get(&source_height) {
            Some(Some(price)) => window.push(*price),
            Some(None) | None => {
                warn!(
                    "Cannot compute fee_actual for height {height}: fee_proposals_window has no \
                     recorded fee_proposal for height {source_height}"
                );
                return None;
            }
        }
    }
    window.sort();
    let mid = window_size_usize / 2;
    let median = if window_size_usize.is_multiple_of(2) {
        // Even: average of the two middle values, rounded down.
        // Overflow-safe averaging: a + (b - a) / 2 (safe because sorted, so b >= a).
        GasPrice(window[mid - 1].0 + (window[mid].0 - window[mid - 1].0) / 2)
    } else {
        window[mid]
    };
    Some(median)
}
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L170-170)
```rust
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L308-319)
```rust
    fn record_fee_proposal(&mut self, height: BlockNumber, fee_proposal_fri: Option<GasPrice>) {
        self.fee_proposals_window.insert(height, fee_proposal_fri);
    }

    fn prune_fee_proposals_window(&mut self, current_height: BlockNumber) {
        let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
        let cutoff = BlockNumber(current_height.0.saturating_sub(window_size));
        // Per `BTreeMap::split_off` docs: "Splits the collection into two at the given key.
        // Returns everything after the given key, including the key." Reassigning the returned
        // half back keeps `[cutoff, ..)` and drops everything below.
        self.fee_proposals_window = self.fee_proposals_window.split_off(&cutoff);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L409-409)
```rust
            fee_proposal_fri: init.fee_proposal_fri,
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L55-77)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```
