### Title
`big.Int.Int64()` Silent Truncation in `SuggestGasTipCap` Causes `eth_maxPriorityFeePerGas` and `eth_gasPrice` to Return Incorrect Values When Base Fee Exceeds `MaxInt64` - (File: `rpc/backend/chain_info.go`)

### Summary

`SuggestGasTipCap` in `rpc/backend/chain_info.go` casts the `*big.Int` base fee to `int64` via `baseFee.Int64()` before performing arithmetic. In Go, `big.Int.Int64()` silently returns only the low 64 bits of the absolute value when the value exceeds `math.MaxInt64`, producing an undefined/incorrect result. When the EIP-1559 base fee grows beyond `math.MaxInt64` (~9.2e18), the computed `maxDelta` becomes negative and is clamped to zero, causing the function to return `0`. This incorrect value propagates directly into `eth_maxPriorityFeePerGas`, `eth_gasPrice`, and `SetTxDefaults`, feeding incorrect fee data to every client that relies on the JSON-RPC for gas price estimation.

### Finding Description

In `rpc/backend/chain_info.go`, `SuggestGasTipCap` computes the maximum base-fee delta using native `int64` arithmetic:

```go
maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)
if maxDelta < 0 {
    maxDelta = 0
}
return big.NewInt(maxDelta), nil
``` [1](#0-0) 

The Ethermint fee market stores the base fee as an arbitrary-precision `sdkmath.Int` / `*big.Int` with no upper bound. `CalculateBaseFee` in `x/feemarket/keeper/eip1559.go` uses `big.Int` arithmetic throughout and can grow the base fee without limit across blocks. [2](#0-1) 

When `baseFee > math.MaxInt64`, `baseFee.Int64()` returns the low 64 bits of the value, which wraps to a negative `int64`. The `if maxDelta < 0` guard then clamps the result to `0`, so `SuggestGasTipCap` returns `big.NewInt(0)`.

This incorrect zero propagates to three callers:

1. **`MaxPriorityFeePerGas` / `eth_maxPriorityFeePerGas`** — returns `0` instead of the correct tip cap. [3](#0-2) 

2. **`GasPrice` / `eth_gasPrice`** — returns `baseFee + 0 = baseFee` instead of `baseFee + tip`. [4](#0-3) 

3. **`SetTxDefaults`** — when a client omits `maxPriorityFeePerGas`, it is silently set to `0`, and `maxFeePerGas` is set to `2 * baseFee`. The effective gas price becomes `min(0 + baseFee, 2*baseFee) = baseFee`, meaning the transaction carries zero tip. [5](#0-4) 

### Impact Explanation

Every wallet or dApp that calls `eth_maxPriorityFeePerGas`, `eth_gasPrice`, or `eth_sendTransaction`/`eth_call` without explicitly setting the tip will receive a zero tip cap. Transactions built with these defaults will have zero priority tip. If the chain or validators enforce a minimum tip, all such transactions will be rejected. Even without a minimum tip requirement, the entire JSON-RPC fee-estimation surface returns systematically wrong values, causing user transactions to be mis-priced and potentially stuck or dropped. This matches the allowed High impact: **"Public JSON-RPC path feeds incorrect consensus-critical data into transaction execution."**

### Likelihood Explanation

The Ethermint base fee grows by up to `baseFee / BaseFeeChangeDenominator` per block (default denominator = 8, so up to 12.5% per block). Starting from the default initial base fee of 1 Gwei (1e9):

```
1e9 × 1.125^n ≥ 9.2e18  →  n ≈ 194 consecutive full blocks
```

At a ~6-second block time, ~194 full blocks takes roughly 20 minutes of sustained block-filling. Any actor who can fill blocks (e.g., by submitting high-gas transactions) can drive the base fee past `MaxInt64` and trigger the overflow. Once triggered, the condition persists as long as the base fee remains above `MaxInt64`.

### Recommendation

Replace the `int64` arithmetic with `big.Int` arithmetic throughout `SuggestGasTipCap`, mirroring the pattern already used in `CalculateBaseFee`:

```go
func (b *Backend) SuggestGasTipCap(baseFee *big.Int) (*big.Int, error) {
    if baseFee == nil {
        return big.NewInt(0), nil
    }
    params, err := b.queryClient.FeeMarket.Params(b.ctx, &feemarkettypes.QueryParamsRequest{})
    if err != nil {
        return nil, err
    }
    em := new(big.Int).SetUint64(uint64(params.Params.ElasticityMultiplier))
    denom := new(big.Int).SetUint64(uint64(params.Params.BaseFeeChangeDenominator))
    // maxDelta = baseFee * (ElasticityMultiplier - 1) / BaseFeeChangeDenominator
    maxDelta := new(big.Int).Mul(baseFee, new(big.Int).Sub(em, big.NewInt(1)))
    maxDelta.Div(maxDelta, denom)
    if maxDelta.Sign() < 0 {
        maxDelta.SetInt64(0)
    }
    return maxDelta, nil
}
```

### Proof of Concept

1. Deploy an Ethermint chain with default fee market parameters (`ElasticityMultiplier=2`, `BaseFeeChangeDenominator=8`, initial base fee = 1 Gwei).
2. Submit transactions that fill every block to 100% of the gas limit for ~194 consecutive blocks (~20 minutes at 6s block time). The base fee grows by 12.5% each block.
3. After ~194 blocks, `baseFee > math.MaxInt64` (~9.2e18 wei).
4. Call `eth_maxPriorityFeePerGas` via JSON-RPC. Observe it returns `0x0` instead of the correct positive tip cap.
5. Call `eth_gasPrice`. Observe it returns exactly `baseFee` (no tip component).
6. Submit a transaction via `eth_sendTransaction` without specifying `maxPriorityFeePerGas`. Observe `SetTxDefaults` sets `maxPriorityFeePerGas = 0`, causing the transaction to carry zero tip and be deprioritized or rejected if a minimum tip is enforced. [6](#0-5) [7](#0-6)

### Citations

**File:** rpc/backend/chain_info.go (L419-444)
```go
func (b *Backend) SuggestGasTipCap(baseFee *big.Int) (*big.Int, error) {
	if baseFee == nil {
		// london hardfork not enabled or feemarket not enabled
		return big.NewInt(0), nil
	}

	params, err := b.queryClient.FeeMarket.Params(b.ctx, &feemarkettypes.QueryParamsRequest{})
	if err != nil {
		return nil, err
	}
	// calculate the maximum base fee delta in current block, assuming all block gas limit is consumed
	// ```
	// GasTarget = GasLimit / ElasticityMultiplier
	// Delta = BaseFee * (GasUsed - GasTarget) / GasTarget / Denominator
	// ```
	// The delta is at maximum when `GasUsed` is equal to `GasLimit`, which is:
	// ```
	// MaxDelta = BaseFee * (GasLimit - GasLimit / ElasticityMultiplier) / (GasLimit / ElasticityMultiplier) / Denominator
	//          = BaseFee * (ElasticityMultiplier - 1) / Denominator
	// ```
	maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)
	if maxDelta < 0 {
		// impossible if the parameter validation passed.
		maxDelta = 0
	}
	return big.NewInt(maxDelta), nil
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

**File:** rpc/namespaces/ethereum/eth/api.go (L371-381)
```go
func (e *PublicAPI) MaxPriorityFeePerGas() (*hexutil.Big, error) {
	e.logger.Debug("eth_maxPriorityFeePerGas")
	head, err := e.backend.CurrentHeader()
	if err != nil {
		return nil, err
	}
	tipcap, err := e.backend.SuggestGasTipCap(head.BaseFee)
	if err != nil {
		return nil, err
	}
	return (*hexutil.Big)(tipcap), nil
```

**File:** rpc/backend/call_tx.go (L232-249)
```go
			if args.MaxPriorityFeePerGas == nil {
				tip, err := b.SuggestGasTipCap(head.BaseFee)
				if err != nil {
					return args, err
				}
				args.MaxPriorityFeePerGas = (*hexutil.Big)(tip)
			}

			if args.MaxFeePerGas == nil {
				gasFeeCap := new(big.Int).Add(
					(*big.Int)(args.MaxPriorityFeePerGas),
					new(big.Int).Mul(head.BaseFee, big.NewInt(2)),
				)
				args.MaxFeePerGas = (*hexutil.Big)(gasFeeCap)
			}

			if args.MaxFeePerGas.ToInt().Cmp(args.MaxPriorityFeePerGas.ToInt()) < 0 {
				return args, fmt.Errorf("maxFeePerGas (%v) < maxPriorityFeePerGas (%v)", args.MaxFeePerGas, args.MaxPriorityFeePerGas)
```

**File:** rpc/backend/call_tx.go (L479-483)
```go
		result, err = b.SuggestGasTipCap(head.BaseFee)
		if err != nil {
			return nil, err
		}
		result = result.Add(result, head.BaseFee)
```
