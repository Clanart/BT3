### Title
RPC `CalcBaseFee` Uses Raw `gasUsed` Instead of Consensus-Adjusted `GetBlockGasWanted`, Causing Systematically Incorrect Base Fee Predictions - (`rpc/backend/utils.go`)

---

### Summary

The JSON-RPC backend's `CalcBaseFee` function and `NextBaseFee` endpoint compute the next base fee using raw `gasUsed` from block results, while the consensus `CalculateBaseFee` uses the `MinGasMultiplier`-adjusted `GetBlockGasWanted` value stored in EndBlock. When `MinGasMultiplier > 0`, these two values diverge, causing the RPC to systematically underreport the next base fee. Clients that rely on `eth_baseFee` or `eth_feeHistory` to set `maxFeePerGas` will construct transactions that are rejected by the ante handler.

---

### Finding Description

**Consensus path** (`x/feemarket/keeper/abci.go` EndBlock → `x/feemarket/keeper/eip1559.go` CalculateBaseFee):

In `EndBlock`, the gas value stored for the next block's base fee calculation is:

```go
minGasMultiplier := k.GetParams(ctx).MinGasMultiplier
limitedGasWanted := sdkmath.LegacyNewDec(gw).Mul(minGasMultiplier)
gasWanted = sdkmath.LegacyMaxDec(limitedGasWanted, sdkmath.LegacyNewDec(gasUsed)).TruncateInt().Uint64()
k.SetBlockGasWanted(ctx, gasWanted)
``` [1](#0-0) 

This stores `max(blockGasWanted × MinGasMultiplier, blockGasUsed)` — not raw `gasUsed`.

In the next `BeginBlock`, `CalculateBaseFee` reads this adjusted value:

```go
parentGasUsed := k.GetBlockGasWanted(ctx)
``` [2](#0-1) 

and uses it to compute the new base fee via the EIP-1559 formula. [3](#0-2) 

---

**RPC path** (`rpc/backend/chain_info.go` `NextBaseFee` → `rpc/backend/utils.go` `CalcBaseFee`):

`NextBaseFee` computes `gasUsed` directly from block results:

```go
gasUsed, err := computeGasUsed(blockRes)
// ...
header := ethtypes.Header{
    GasUsed:  gasUsed,
    ...
}
nextBaseFee, err := CalcBaseFee(cfg, &header, feeParams.Params)
``` [4](#0-3) 

`CalcBaseFee` then uses `parent.GasUsed` directly — with no `MinGasMultiplier` adjustment:

```go
parentGasTarget := parent.GasLimit / uint64(p.ElasticityMultiplier)
if parent.GasUsed == parentGasTarget {
    return new(big.Int).Set(parent.BaseFee), nil
}
if parent.GasUsed > parentGasTarget {
    ...
``` [5](#0-4) 

The same raw-`gasUsed` path is taken in `processBlock` for `eth_feeHistory`:

```go
gasUsed, ok := (*ethBlock)["gasUsed"].(hexutil.Uint64)
// ...
header.GasUsed = uint64(gasUsed)
// ...
nextBaseFee, err := CalcBaseFee(cfg, &header, params.Params)
``` [6](#0-5) 

---

### Impact Explanation

When `MinGasMultiplier > 0` and `blockGasWanted × MinGasMultiplier > blockGasUsed` (i.e., blocks are not full and the multiplier floor is active), the consensus stores a `GetBlockGasWanted` value strictly greater than raw `gasUsed`. The consensus `CalculateBaseFee` therefore computes a **higher** base fee than the RPC's `CalcBaseFee`. The RPC's `eth_baseFee` and `eth_feeHistory` endpoints return a base fee that is lower than what the chain will actually enforce. Any client that uses these endpoints to set `maxFeePerGas` will submit transactions whose effective fee cap falls below the actual consensus base fee, causing the ante handler to reject them as under-priced. This is a public JSON-RPC path feeding incorrect consensus-critical data into transaction construction and execution.

---

### Likelihood Explanation

The discrepancy is triggered whenever `MinGasMultiplier` is set to a non-zero value (the parameter exists precisely to prevent base-fee manipulation via artificially low gas usage). Any chain operator who enables this protection activates the bug. The condition `blockGasWanted × MinGasMultiplier > blockGasUsed` is common in normal operation when transactions request more gas than they consume. No special privileges or attack setup are required — any user calling `eth_baseFee` or `eth_feeHistory` on such a chain receives the wrong value.

---

### Recommendation

Replace the raw `gasUsed` input in `CalcBaseFee` (RPC) with the same adjusted value that consensus uses. Either:

1. Query `GetBlockGasWanted` from the feemarket module at the relevant block height and pass it as `GasUsed` in the header, or
2. Apply the same `max(gasWanted × MinGasMultiplier, gasUsed)` formula inside `CalcBaseFee` / `NextBaseFee` before computing the base fee delta.

This mirrors the fix pattern from the external report: compute the correct intermediate value before feeding it into the rate/fee formula, rather than using the raw observed value.

---

### Proof of Concept

1. Deploy a chain with `MinGasMultiplier = 0.5`.
2. In block N, include transactions whose gas limits sum to 1,000,000 but actual gas used is only 100,000. Consensus EndBlock stores `GetBlockGasWanted = max(500,000, 100,000) = 500,000`.
3. In block N+1 BeginBlock, consensus `CalculateBaseFee` uses `parentGasUsed = 500,000` and computes a base fee reflecting a block that is 50% full.
4. A client calls `eth_baseFee`. The RPC's `NextBaseFee` calls `computeGasUsed(blockRes)` → 100,000, then `CalcBaseFee` with `GasUsed = 100,000`, computing a base fee reflecting a block that is only 10% full — significantly lower.
5. The client sets `maxFeePerGas` to the RPC-reported value and submits a transaction. The ante handler compares against the consensus base fee (higher) and rejects the transaction as under-priced.

### Citations

**File:** x/feemarket/keeper/abci.go (L72-75)
```go
	minGasMultiplier := k.GetParams(ctx).MinGasMultiplier
	limitedGasWanted := sdkmath.LegacyNewDec(gw).Mul(minGasMultiplier)
	gasWanted = sdkmath.LegacyMaxDec(limitedGasWanted, sdkmath.LegacyNewDec(gasUsed)).TruncateInt().Uint64()
	k.SetBlockGasWanted(ctx, gasWanted)
```

**File:** x/feemarket/keeper/eip1559.go (L57-57)
```go
	parentGasUsed := k.GetBlockGasWanted(ctx)
```

**File:** x/feemarket/keeper/eip1559.go (L80-104)
```go
	if parentGasUsed > parentGasTarget {
		// If the parent block used more gas than its target, the baseFee should
		// increase.
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - parentGasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, parentGasTargetBig)
		baseFeeDelta := ethermint.BigMax(
			x.Div(y, baseFeeChangeDenominator),
			common.Big1,
		)

		return x.Add(parentBaseFee, baseFeeDelta)
	}

	// Otherwise if the parent block used less gas than its target, the baseFee
	// should decrease.
	gasUsedDelta := new(big.Int).SetUint64(parentGasTarget - parentGasUsed)
	x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
	y := x.Div(x, parentGasTargetBig)
	baseFeeDelta := x.Div(y, baseFeeChangeDenominator)

	// Set global min gas price as lower bound of the base fee, transactions below
	// the min gas price don't even reach the mempool.
	minGasPrice := params.MinGasPrice.TruncateInt().BigInt()
	return ethermint.BigMax(x.Sub(parentBaseFee, baseFeeDelta), minGasPrice)
```

**File:** rpc/backend/chain_info.go (L151-170)
```go
	gasUsed, err := computeGasUsed(blockRes)
	if err != nil {
		return nil, err
	}

	feeParams, err := b.queryClient.FeeMarket.Params(
		rpctypes.ContextWithHeight(blockHeight),
		&feemarkettypes.QueryParamsRequest{},
	)
	if err != nil {
		return nil, err
	}

	header := ethtypes.Header{
		Number:   big.NewInt(blockHeight),
		BaseFee:  blockBaseFee,
		GasLimit: gasLimitUint64,
		GasUsed:  gasUsed,
	}
	nextBaseFee, err := CalcBaseFee(cfg, &header, feeParams.Params)
```

**File:** rpc/backend/utils.go (L131-151)
```go
	parentGasTarget := parent.GasLimit / uint64(p.ElasticityMultiplier)
	// If the parent gasUsed is the same as the target, the baseFee remains unchanged.
	if parent.GasUsed == parentGasTarget {
		return new(big.Int).Set(parent.BaseFee), nil
	}

	var (
		num   = new(big.Int)
		denom = new(big.Int)
	)

	if parent.GasUsed > parentGasTarget {
		// If the parent block used more gas than its target, the baseFee should increase.
		// max(1, parentBaseFee * gasUsedDelta / parentGasTarget / baseFeeChangeDenominator)
		num.SetUint64(parent.GasUsed - parentGasTarget)
		num.Mul(num, parent.BaseFee)
		num.Div(num, denom.SetUint64(parentGasTarget))
		num.Div(num, denom.SetUint64(uint64(p.BaseFeeChangeDenominator)))
		baseFeeDelta := ethermint.BigMax(num, common.Big1)
		return num.Add(parent.BaseFee, baseFeeDelta), nil
	}
```

**File:** rpc/backend/utils.go (L186-211)
```go
	gasUsed, ok := (*ethBlock)["gasUsed"].(hexutil.Uint64)
	if !ok {
		return fmt.Errorf("invalid gas used type: %T", (*ethBlock)["gasUsed"])
	}

	if cfg.IsLondon(big.NewInt(blockHeight + 1)) {
		var header ethtypes.Header
		header.Number = new(big.Int).SetInt64(blockHeight)
		baseFee, ok := (*ethBlock)["baseFeePerGas"].(*hexutil.Big)
		if !ok || baseFee == nil {
			header.BaseFee = big.NewInt(0)
		} else {
			header.BaseFee = baseFee.ToInt()
		}
		header.GasLimit = uint64(gasLimitUint64)
		header.GasUsed = uint64(gasUsed)
		ctx := types.ContextWithHeight(blockHeight)
		params, err := b.queryClient.FeeMarket.Params(ctx, &feemarkettypes.QueryParamsRequest{})
		if err != nil {
			return err
		}
		nextBaseFee, err := CalcBaseFee(cfg, &header, params.Params)
		if err != nil {
			return err
		}
		targetOneFeeHistory.NextBaseFee = nextBaseFee
```
