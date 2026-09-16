This confirms the mechanics needed for the analog. I have enough to write the finding.

### Title
Cross-parameter governance consistency check for KIP-71 base fee bounds uses stale state, allowing ratification of `LowerBoundBaseFee > UpperBoundBaseFee` - (File: kaiax/gov/headergov/impl/header.go)

### Summary
`checkConsistency` in `headerGovModule` is meant to enforce the invariant `LowerBoundBaseFee <= UpperBoundBaseFee` for KIP-71 dynamic base fee governance votes, analogous to the Lybra `LybraConfigurator` bug where the safe/bad collateral ratio relationship was checked against a value that could change out from under the check. Here, each vote is validated only against the *currently effective* (previous-epoch) parameter set rather than against the other pending vote submitted within the same epoch, so two independent votes that each individually pass validation can jointly ratify an inconsistent pair of bounds.

### Finding Description
`checkConsistency` handles `gov.Kip71LowerBoundBaseFee` and `gov.Kip71UpperBoundBaseFee` as two separate cases, each comparing the newly proposed value against `h.GetParamSet(blockNum)` — the parameter set ratified from the *previous* epoch: [1](#0-0) 

`GetParamSet(blockNum)` always returns state from `PrevEpochStart`, i.e., parameters ratified before the current epoch began, not any votes cast within the current (ongoing) epoch: [2](#0-1) 

Per the module's own documentation, all votes cast within an epoch are collected and, for each parameter, the *last* vote in the epoch is what gets ratified together at the epoch boundary: [3](#0-2) 

Because the two bound checks are evaluated independently against the stale previous-epoch values (not against each other's pending value), a governing node (or, in `none` mode, the last voter for each key) can submit two votes in the same epoch that are each individually consistent with the old state but jointly violate the invariant once ratified together:

1. Epoch starts with effective `Lower=25e9`, `Upper=750e9` (defaults).
2. Vote A: `Kip71LowerBoundBaseFee = 700e9`. Check: `700e9 > Upper(750e9)`? No → passes.
3. Vote B (later block, same epoch): `Kip71UpperBoundBaseFee = 100e9`. Check: `100e9 < Lower(25e9, still the OLD value)`? No → passes.
4. At the epoch boundary both votes are ratified together, producing `Lower=700e9 > Upper=100e9`.

### Impact Explanation
`KIP71Config.NextMagmaBlockBaseFee`, which computes the EIP-1559-style dynamic base fee used for every block's gas pricing, assumes `LowerBoundBaseFee <= UpperBoundBaseFee` and clamps the parent base fee into that range before computing deltas: [4](#0-3) 

With `Lower > Upper`, the clamping logic (`if parentBaseFee >= Upper { parentBaseFee = Upper } else if parentBaseFee <= Lower { parentBaseFee = Lower }`) produces contradictory/undefined bounds, and downstream comparisons against `upperBoundBaseFee`/`lowerBoundBaseFee` for early-return shortcuts can behave inconsistently across code paths, potentially causing all nodes to derive the same (deterministic but nonsensical) base fee, or, more importantly, breaking the intended lower/upper protection of the fee market — e.g. base fee could get stuck at a state that no longer serves as a meaningful floor/ceiling for gas pricing across the network. Since all honest nodes execute the same deterministic function, this would not cause a chain split by itself, but it would corrupt the fee market parameters network-wide until the DAO/governing node issues a corrective vote, and in the interim could allow the base fee to be pinned in a way that either starves the network of fee revenue (burn/reward miscalculation) or makes transactions unexpectedly cheap/expensive relative to intended KIP-71 policy — a governance-parameter integrity violation reachable via ordinary `governance_vote` submissions from an authorized voter.

### Likelihood Explanation
This requires control of a governance voting key (the governing node in `single` mode, or any GC member able to cast the last vote per parameter in `none` mode) — the same trust tier as the original Lybra DAO/timelock roles for `setSafeCollateralRatio`/`setBadCollateralRatio`. Mainnet and Kairos operate in `single` mode, so a single governing node's two ordinary transactions (votes) within one epoch are sufficient; no consensus-message forgery, p2p exploitation, or protocol-level bypass is needed, and `VerifyGov`/`VerifyVote` do not perform a final consolidated sanity check across all votes ratified at the epoch boundary: [5](#0-4) 

### Recommendation
When ratifying votes at the epoch boundary (in `getExpectedGovernance` or an equivalent final-consistency pass), re-validate the *combined* resulting parameter set — specifically re-check `LowerBoundBaseFee <= UpperBoundBaseFee` using the final values that will actually take effect together, not just each vote against the stale previous-epoch baseline. Alternatively, when checking one of the pair, also account for any pending vote on the other key within the same epoch before ratification.

### Proof of Concept
1. Set governance mode to `single` with a designated `governingnode`.
2. At epoch start, effective params: `LowerBoundBaseFee=25000000000`, `UpperBoundBaseFee=750000000000`.
3. Governing node calls `governance_vote("kip71.lowerboundbasefee", 700000000000)` — passes `checkConsistency` since `700e9 <= 750e9` (old Upper).
4. In a later block of the same epoch, governing node calls `governance_vote("kip71.upperboundbasefee", 100000000000)` — passes `checkConsistency` since `100e9 >= 25e9` (old Lower).
5. At the next epoch boundary, `header.Governance` ratifies both: `Lower=700e9`, `Upper=100e9`, an inconsistent pair, which becomes the active `KIP71Config` for the next epoch and feeds directly into `NextMagmaBlockBaseFee` for every subsequent block.

### Citations

**File:** kaiax/gov/headergov/impl/header.go (L118-153)
```go
func (h *headerGovModule) VerifyGov(header *types.Header) error {
	// (1)
	if header.Number.Uint64()%h.epoch != 0 {
		if len(header.Governance) > 0 {
			logger.Error("governance is not allowed in non-epoch block", "num", header.Number.Uint64())
			return ErrGovInNonEpochBlock
		} else {
			return nil
		}
	}

	// (2), (3)
	expected := h.getExpectedGovernance(header.Number.Uint64())
	if len(header.Governance) == 0 {
		if len(expected.Items()) != 0 {
			return ErrGovVerification
		}

		return nil
	}

	// (4)
	var gb headergov.GovBytes = header.Governance
	actual, err := gb.ToGovData()
	if err != nil {
		logger.Error("DeserializeHeaderGov error", "num", header.Number.Uint64(), "governance", gb, "err", err)
		return err
	}

	// (5)
	if !reflect.DeepEqual(expected, actual) {
		logger.Error("Governance mismatch", "expected", expected, "actual", actual)
		return ErrGovVerification
	}

	return nil
```

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
```go
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
			return nil
		}
```

**File:** kaiax/gov/headergov/impl/getter.go (L11-23)
```go
func (h *headerGovModule) GetParamSet(blockNum uint64) gov.ParamSet {
	h.mu.RLock()
	defer h.mu.RUnlock()

	prevEpochStart := PrevEpochStart(blockNum, h.epoch, h.isKoreHF(blockNum))
	gh := h.history
	gp, err := gh.Search(prevEpochStart)
	if err != nil {
		logger.Warn("No param set", "blockNum", blockNum, "prevEpochStart", prevEpochStart)
		return *gov.GetDefaultGovernanceParamSet()
	}
	return gp
}
```

**File:** kaiax/gov/headergov/README.md (L44-50)
```markdown
The ratification condition is determined by the `governance.governancemode` parameter. Mainnet and Kairos both operate in `single` mode. There are two governance modes:

- `none` mode: all members of the GC can vote. For each governance parameter, the last vote in the epoch will be ratified.
- `single` mode: only one member of the GC, stipulated in the parameter `governance.governingnode`, can vote. All valid votes from the governing node in the epoch are ratified in block order. For each governance parameter, the last vote in the epoch will be ratified.

Parameter change ratified at `k*epoch` block takes effect starting from `(k+1)*epoch` block.
It is worth noting that the effective time of the ratification is `(k+1)*epoch + 1` before Kore.
```

**File:** params/kip71_config.go (L80-128)
```go
	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}

	// upper gas limit cut off the impulse of used gas to upper bound
	parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
	if parentGasUsed == gasTarget {
		return makeEvenByFloor(parentBaseFee)
	} else if parentGasUsed > gasTarget {
		// shortcut. If parentBaseFee is already reached upperbound, do not calculate.
		if parentBaseFee.Cmp(upperBoundBaseFee) == 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	} else {
		// shortcut. If parentBaseFee is already reached lower bound, do not calculate.
		if parentBaseFee.Cmp(lowerBoundBaseFee) == 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
```
