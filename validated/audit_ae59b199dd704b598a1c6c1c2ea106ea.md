### Title
Missing cross-validation between `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` governance parameters allows an inconsistent bound configuration - (File: `kaiax/gov/param.go`)

### Summary
Kaia's dynamic base fee mechanism (KIP-71 / Magma) is governed by two related parameters, `LowerBoundBaseFee` and `UpperBoundBaseFee`, which are supposed to bound the base fee from below and above respectively. Like the Mochi `liquidationFactor`/`maxCollateralFactor` pair, these two values must maintain a safe ordering relationship (`lowerBound < upperBound`) for the fee mechanism to function correctly, but the governance parameter framework validates each parameter independently with no check that this relationship holds.

### Finding Description
Each governance parameter in `kaiax/gov/param.go` is registered with its own independent `FormatChecker`. For the KIP-71 bound parameters, both use `noopFormatChecker`, meaning no numeric constraint is applied to either value in isolation, and — critically — there is no cross-parameter check ensuring `LowerBoundBaseFee < UpperBoundBaseFee`: [1](#0-0) [2](#0-1) 

Governance votes are applied and validated one parameter at a time via `PartialParamSet.Add`, which only runs the single parameter's own `Canonicalizer`/`FormatChecker` — there is no atomic, whole-`ParamSet` sanity check across related fields: [3](#0-2) 

These two values are then fed directly into `NextMagmaBlockBaseFee`, which clamps the parent base fee against `upperBoundBaseFee` and `lowerBoundBaseFee` and computes the next block's base fee within these bounds: [4](#0-3) 

If `LowerBoundBaseFee` is voted to be greater than `UpperBoundBaseFee` (each individually valid under its own no-op format checker), the clamping logic in `NextMagmaBlockBaseFee` produces contradictory behavior: the base fee gets clamped down to `upperBoundBaseFee` when high, then immediately re-clamped up to `lowerBoundBaseFee` (which is greater) on the next evaluation, so the "bounds" no longer bound each other — directly analogous to the Mochi bug where the liquidation factor (safety exit threshold) was set below the collateral factor (entry threshold), breaking the intended safety margin between two related economic parameters.

### Impact Explanation
An inconsistent bound configuration undermines the core guarantee of the KIP-71 dynamic fee mechanism intended to keep the base fee within a sane, predictable economic range for all transaction senders on the network. Because `UpperBoundGasPrice`/`LowerBoundGasPrice` RPC methods and fee suggestions surface these same governance values directly to users and wallets: [5](#0-4) 

a misconfigured bound pair can mislead every transaction sender's fee expectations and cause the effective base fee to behave erratically (oscillating between the swapped bounds), impacting fee correctness network-wide rather than a single user's position.

### Likelihood Explanation
This requires a governance vote to set the two KIP-71 parameters inconsistently, which is a privileged action (governing node or majority-vote in ballot mode) rather than something any arbitrary unprivileged sender can trigger directly. However, because neither the single-field `FormatChecker`s nor the vote-application path (`PartialParamSet.Add`) perform any cross-field consistency check, the vote-count/format-check protections that governance changes normally rely on to reject unsafe values entirely fail to catch this specific unsafe combination, so it can be introduced unintentionally with a routine governance change, not just through malicious intent.

### Recommendation
Add a cross-parameter consistency check (e.g., in `PartialParamSet.SetFromMap`/`Add` or as part of paramset assembly) that rejects any governance change resulting in `LowerBoundBaseFee >= UpperBoundBaseFee`, mirroring the recommended fix pattern of enforcing a required ordering/safety margin between related threshold parameters.

### Proof of Concept
1. Submit (or accumulate, in ballot governance mode) votes to set `kip71.lowerboundbasefee = 800000000000` while `kip71.upperboundbasefee` remains at the existing default `750000000000` (or vice versa: lower the upper bound below the current lower bound).
2. Each vote passes its individual `FormatChecker` (`noopFormatChecker` for both) shown at `kaiax/gov/param.go:335-367`, and no code path checks the two values against each other before they are committed to the `ParamSet`.
3. On the next block, `NextMagmaBlockBaseFee` (`params/kip71_config.go:58-129`) uses these now-contradictory bounds, clamping the base fee inconsistently between the swapped lower/upper values, breaking the fee-bounding invariant relied upon by every transaction sender submitting to the network.

### Citations

**File:** kaiax/gov/param.go (L335-345)
```go
	Kip71LowerBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.LowerBoundBaseFee, nil
		},
		DefaultValue: uint64(25000000000),
	},
```

**File:** kaiax/gov/param.go (L357-367)
```go
	Kip71UpperBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.UpperBoundBaseFee, nil
		},
		DefaultValue: uint64(750000000000),
	},
```

**File:** kaiax/gov/paramset.go (L209-226)
```go
func (p PartialParamSet) Add(name string, value any) error {
	param, ok := Params[ParamName(name)]
	if !ok {
		return ErrInvalidParamName
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		return err
	}

	if !param.FormatChecker(cv) {
		return ErrInvalidParamValue
	}

	p[ParamName(name)] = cv
	return nil
}
```

**File:** params/kip71_config.go (L58-86)
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
```

**File:** node/cn/api_backend.go (L333-351)
```go
func (b *CNAPIBackend) UpperBoundGasPrice(ctx context.Context) *big.Int {
	bignum := b.CurrentBlock().Number()
	pset := b.cn.govModule.GetParamSet(bignum.Uint64() + 1)
	if b.cn.chainConfig.IsMagmaForkEnabled(bignum) {
		return new(big.Int).SetUint64(pset.UpperBoundBaseFee)
	} else {
		return new(big.Int).SetUint64(pset.UnitPrice)
	}
}

func (b *CNAPIBackend) LowerBoundGasPrice(ctx context.Context) *big.Int {
	bignum := b.CurrentBlock().Number()
	pset := b.cn.govModule.GetParamSet(bignum.Uint64() + 1)
	if b.cn.chainConfig.IsMagmaForkEnabled(bignum) {
		return new(big.Int).SetUint64(pset.LowerBoundBaseFee)
	} else {
		return new(big.Int).SetUint64(pset.UnitPrice)
	}
}
```
