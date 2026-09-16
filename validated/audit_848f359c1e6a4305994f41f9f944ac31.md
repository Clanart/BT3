### Title
Division by zero panic in `KIP71Config.NextMagmaBlockBaseFee` when `kip71.gastarget` governance parameter is set to 0 - (File: params/kip71_config.go)

### Summary
The Magma base-fee formula (KIP-71) computes the next block's base fee using `kc.GasTarget` as a divisor without ever checking that it is non-zero. Unlike `BaseFeeDenominator`, which has an explicit `== 0` guard, `GasTarget` is validated only by a `noopFormatChecker` in the governance parameter table, so a governance vote can set `kip71.gastarget` to `0`. Once in effect, every node that computes or verifies the next base fee (`VerifyMagmaHeader` → `NextMagmaBlockBaseFee`, called from `blockchain/block_validator.go` for every block, and from `work/worker.go`, `blockchain/chain_makers.go`, `blockchain/tx_pool.go`, and the `gasprice`/`feehistory` RPC services) will hit a `big.Int.Div` with divisor `0`, which panics at runtime (Go's `math/big.Int.Div` explicitly panics on division by zero).

### Finding Description
`NextMagmaBlockBaseFee` in [1](#0-0)  reads `gasTarget := kc.GasTarget` with no zero check (contrast with the explicit zero-guard for `BaseFeeDenominator` a few lines above it, at [2](#0-1) ). When `parentGasUsed != gasTarget`, the function divides by `gasTarget` twice — once in the "increase" branch and once in the "decrease" branch: [3](#0-2) [4](#0-3) 

If `gasTarget == 0`, and `parentGasUsed != 0` (true for essentially any block that consumes gas), the branch is entered and `x.Div(x, new(big.Int).SetUint64(gasTarget))` is executed with divisor `0`, which is a documented runtime panic in Go's `math/big` package.

The governance parameter `kip71.gastarget` (`Kip71GasTarget`) is registered with `FormatChecker: noopFormatChecker`, i.e., no validation at all, at [5](#0-4) , while the sibling parameter `Kip71BaseFeeDenominator` explicitly requires `v != 0` at [6](#0-5) . This asymmetry means a vote setting `gastarget=0` passes `NewVoteData`'s canonicalization/format check in [7](#0-6)  and is accepted by `VerifyVote` in [8](#0-7) , which only checks voter/proposer identity, deprecation, and `checkConsistency` — none of which reject `gastarget=0`.

Once the parameter value takes effect in the `ParamSet` for a block, `NextMagmaBlockBaseFee` is invoked by `VerifyMagmaHeader` from `blockchain/block_validator.go` for every incoming block header validated by every full node/validator on the network, as well as by block-production code (`work/worker.go`, `blockchain/chain_makers.go`), the tx pool's base-fee estimation (`blockchain/tx_pool.go`), and RPC gas-price/fee-history services (`node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`). All of these paths will panic simultaneously once the malicious parameter is active, crashing every node processing blocks/transactions on the chain — a full network halt.

### Impact Explanation
This is a network-wide denial of service: once `kip71.gastarget=0` becomes the effective governance parameter, all full nodes and validators panic when validating the very next non-empty block (via `VerifyMagmaHeader`), and also when building blocks or serving `eth_gasPrice`/`eth_feeHistory` RPC calls. This halts block production/consensus across the entire network until the software is patched or the offending state is manually rolled back — a Critical availability impact analogous to the TFLite `TransposeConv` division-by-zero (attacker-controlled zero divisor reaching an unguarded division, crashing the process).

### Likelihood Explanation
The trigger requires a single governance vote setting `kip71.gastarget` to `0` to be accepted and become the effective parameter (via the standard `header.Vote` / governance mechanism used for all KIP-71 parameters, e.g., `basefeedenominator`, `lowerboundbasefee`, etc., which are routinely voted on in this codebase's own tests, e.g. [9](#0-8) ). No additional exploit complexity is needed beyond the vote itself; the existing `noopFormatChecker` performs zero validation on the value, so the vote passes all current checks. The only constraint is that the voter must be an authorized governance participant (single-governance mode: the governing node; general mode: a council member acting as proposer) — this is the same privilege level used for legitimate KIP-71 parameter updates in the codebase, and the report's rules explicitly allow "governance parameters" as an in-scope reachable path.

### Recommendation
Add a zero-value guard for `GasTarget` in `NextMagmaBlockBaseFee`, mirroring the existing `BaseFeeDenominator` fallback (`params/kip71_config.go:71-76`), e.g. treat `gasTarget == 0` as an invalid/fallback value rather than dividing by it. Additionally, close the gap at the governance-parameter layer by giving `Kip71GasTarget` in `kaiax/gov/param.go` a `FormatChecker` that rejects `0` (consistent with `Kip71BaseFeeDenominator`), so that no vote can ever set an unsafe value in the first place.

### Proof of Concept
1. A governance vote (via `header.Vote`, using the standard `Vote(name, value)` API in `kaiax/gov/headergov/impl/api.go`) is cast and accepted setting `kip71.gastarget = 0`. This passes `NewVoteData`'s canonicalizer/format checker (`noopFormatChecker` always returns `true`) and `VerifyVote`'s consistency checks, none of which reject zero.
2. Once the parameter becomes effective in the `ParamSet` for a subsequent block, any block with `GasUsed != 0` (essentially every real block) triggers `VerifyMagmaHeader` → `NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)`.
3. Inside `NextMagmaBlockBaseFee`, since `parentGasUsed (>0) != gasTarget (0)`, the code enters either the "increase" branch (`parentGasUsed > gasTarget`) and executes:
   ```go
   gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // = parentGasUsed
   x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
   y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // gasTarget == 0 -> panic: division by zero
   ```
4. This causes an immediate Go runtime panic (`runtime error: integer divide by zero`, per `math/big.Int.Div` semantics) in every node processing the block — in block validators, block builders, the tx pool, and gas-price RPC handlers — crashing the entire node process and halting the network.

### Citations

**File:** params/kip71_config.go (L58-129)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}

	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

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
}
```

**File:** kaiax/gov/param.go (L310-323)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.BaseFeeDenominator, nil
		},
		DefaultValue: uint64(20),
	},
```

**File:** kaiax/gov/param.go (L324-334)
```go
	Kip71GasTarget: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.GasTarget, nil
		},
		DefaultValue: uint64(30000000),
	},
```

**File:** kaiax/gov/headergov/vote.go (L29-55)
```go
func NewVoteData(voter common.Address, name string, value any) VoteData {
	param, ok := gov.Params[gov.ParamName(name)]
	if !ok {
		param, ok = gov.ValidatorParams[gov.ParamName(name)]
		if !ok {
			logger.Error("Invalid vote name", "name", name)
			return nil
		}
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		logger.Error("Canonicalize error", "name", name, "value", value, "err", err)
		return nil
	}

	if !param.FormatChecker(cv) {
		logger.Error("Format check error", "name", name, "value", value)
		return nil
	}

	return &voteData{
		voter: voter,
		name:  gov.ParamName(name),
		value: cv,
	}
}
```

**File:** kaiax/gov/headergov/impl/header.go (L61-110)
```go
func (h *headerGovModule) VerifyVote(header *types.Header) error {
	if len(header.Vote) == 0 {
		return nil
	}

	var (
		vb       headergov.VoteBytes = header.Vote
		blockNum                     = header.Number.Uint64()
	)

	vote, err := vb.ToVoteData()
	if err != nil {
		logger.Error("ToVoteData error", "num", blockNum, "vote", vb, "err", err)
		return err
	}

	if gov.DeprecatedAt(vote.Name(), h.ChainConfig.Rules(header.Number)) {
		logger.Error("Vote is deprecated", "num", blockNum, "name", vote.Name())
		return ErrDeprecatedVote
	}

	council, err := h.ValSet.GetCouncil(blockNum)
	if err != nil {
		return err
	}

	// check if the voter is in council
	if !slices.Contains(council, vote.Voter()) {
		return ErrInvalidKeyValue
	}

	// check if Voter is the block proposer.
	author, err := h.Chain.Sealer().Author(header)
	if err != nil {
		return err
	}
	if author != vote.Voter() {
		return ErrInvalidVoter
	}

	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
	}

	return h.checkConsistency(blockNum, vote)
}
```

**File:** kaiax/gov/headergov/impl/header_test.go (L93-98)
```go
		// kip71.*
		{desc: "valid basefeedenominator", vote: headergov.NewVoteData(validVoter, string(gov.Kip71BaseFeeDenominator), uint64(8)), expectedError: nil},
		{desc: "valid gastarget", vote: headergov.NewVoteData(validVoter, string(gov.Kip71GasTarget), uint64(30000000)), expectedError: nil},
		{desc: "valid lowerboundbasefee", vote: headergov.NewVoteData(validVoter, string(gov.Kip71LowerBoundBaseFee), uint64(25000000000)), expectedError: nil},
		{desc: "valid maxblockgasusedforbasefee", vote: headergov.NewVoteData(validVoter, string(gov.Kip71MaxBlockGasUsedForBaseFee), uint64(60000000)), expectedError: nil},
		{desc: "valid upperboundbasefee", vote: headergov.NewVoteData(validVoter, string(gov.Kip71UpperBoundBaseFee), uint64(750000000000)), expectedError: nil},
```
