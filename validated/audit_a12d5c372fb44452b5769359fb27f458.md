### Title
Unchecked `int64` Arithmetic in `SuggestGasTipCap` Produces Silently Incorrect Gas Price Data Fed to JSON-RPC Clients - (File: `rpc/backend/chain_info.go`)

---

### Summary

`SuggestGasTipCap` in `rpc/backend/chain_info.go` performs fee arithmetic using `baseFee.Int64()` and a plain `int64` multiplication. Because `baseFee` is a `*big.Int` with no upper bound, and Go's `big.Int.Int64()` silently truncates to the low 64 bits when the value exceeds `math.MaxInt64`, the computation produces a silently wrong result. The only guard (`if maxDelta < 0`) only catches the subset of overflows that wrap to a negative value; overflows that wrap to a positive value pass through unchecked. The corrupted value is then returned directly to callers of `eth_maxPriorityFeePerGas`, `eth_gasPrice`, and `eth_sendTransaction` (via `SetTxDefaults`), feeding incorrect fee data into transaction construction.

---

### Finding Description

The vulnerable line is:

```go
// rpc/backend/chain_info.go:439
maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)
```

Two distinct overflow paths exist:

**Path 1 — `baseFee.Int64()` silent truncation.**
`baseFee` is a `*big.Int`. Go's `(*big.Int).Int64()` returns the low 64 bits of the two's-complement representation when the value exceeds `math.MaxInt64` (≈ 9.2 × 10¹⁸ wei). It does not return an error and does not panic. If `baseFee` is, for example, `math.MaxInt64 + 1 = 2^63`, `baseFee.Int64()` returns `math.MinInt64` (−9223372036854775808). The subsequent `if maxDelta < 0` guard then clamps the result to 0, so `SuggestGasTipCap` returns 0 — a completely wrong tip cap.

**Path 2 — `int64` multiplication overflow.**
Even when `baseFee` fits in `int64`, the product `baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1)` can overflow. `ElasticityMultiplier` is a `uint32` governance parameter. With the default value of 2 the multiplier is 1, so no overflow occurs at default settings. But if `ElasticityMultiplier` is raised (e.g., to 3 or higher via governance), the product can overflow for any `baseFee` above `math.MaxInt64 / (ElasticityMultiplier - 1)`. Overflows that wrap to a negative value are clamped to 0 by the guard; overflows that wrap to a positive value are returned as-is, producing a wildly incorrect tip cap.

The `if maxDelta < 0` guard at line 440–442 is the only protection and it is insufficient:

```go
if maxDelta < 0 {
    // impossible if the parameter validation passed.
    maxDelta = 0
}
```

The comment "impossible if the parameter validation passed" is incorrect — it does not account for `baseFee` exceeding `int64` range, which is a normal on-chain condition as the base fee grows over time.

---

### Impact Explanation

`SuggestGasTipCap` is called from three public JSON-RPC paths:

1. **`eth_maxPriorityFeePerGas`** (`rpc/namespaces/ethereum/eth/api.go:377`) — returns the corrupted tip cap directly to clients.
2. **`eth_gasPrice`** (`rpc/backend/call_tx.go:479`) — adds the corrupted tip cap to `baseFee` and returns it as the suggested gas price.
3. **`SetTxDefaults`** (`rpc/backend/call_tx.go:233,237`) — when a caller omits `MaxPriorityFeePerGas`, the corrupted value is injected as the default, and `MaxFeePerGas` is then derived from it (`MaxFeePerGas = tip + 2 * baseFee`).

When `baseFee` exceeds `math.MaxInt64`, all three endpoints return 0 as the tip cap. For `eth_gasPrice` this means the returned gas price equals `baseFee` alone (no tip), causing EIP-1559 transactions constructed from this suggestion to carry zero priority fee and potentially be deprioritized or rejected by the mempool. For `SetTxDefaults`, transactions auto-constructed via `eth_sendTransaction` are submitted with `MaxPriorityFeePerGas = 0`, which may cause them to fail mempool admission checks if the chain enforces a minimum tip.

When the multiplication overflows to a positive value (path 2), the returned tip cap is an arbitrary large positive integer, causing `eth_gasPrice` to return an astronomically inflated gas price and `SetTxDefaults` to set `MaxFeePerGas` to an enormous value, potentially causing users to overpay by orders of magnitude.

This maps to the allowed High impact: **"Public JSON-RPC path feeds incorrect consensus-critical data into transaction execution."**

---

### Likelihood Explanation

The base fee in Ethermint grows automatically based on block gas usage — no privileged action is required. Starting from the default of 1 gwei (10⁹ wei), the base fee can increase by up to 12.5% per block (with default `ElasticityMultiplier = 2`, `BaseFeeChangeDenominator = 8`). Under sustained full-block conditions, `baseFee` can reach `math.MaxInt64` (≈ 9.2 × 10⁹ gwei) over time. Any unprivileged user can call `eth_maxPriorityFeePerGas`, `eth_gasPrice`, or `eth_sendTransaction` to trigger the path. No special permissions, keys, or network position are required.

---

### Recommendation

Replace the `int64` arithmetic with `big.Int` arithmetic throughout `SuggestGasTipCap`:

```go
func (b *Backend) SuggestGasTipCap(baseFee *big.Int) (*big.Int, error) {
    if baseFee == nil {
        return big.NewInt(0), nil
    }
    params, err := b.queryClient.FeeMarket.Params(b.ctx, &feemarkettypes.QueryParamsRequest{})
    if err != nil {
        return nil, err
    }
    elasticity := new(big.Int).SetUint64(uint64(params.Params.ElasticityMultiplier))
    denominator := new(big.Int).SetUint64(uint64(params.Params.BaseFeeChangeDenominator))

    // maxDelta = baseFee * (ElasticityMultiplier - 1) / BaseFeeChangeDenominator
    num := new(big.Int).Mul(baseFee, new(big.Int).Sub(elasticity, big.NewInt(1)))
    maxDelta := new(big.Int).Div(num, denominator)
    if maxDelta.Sign() < 0 {
        maxDelta.SetInt64(0)
    }
    return maxDelta, nil
}
```

---

### Proof of Concept

Assume `baseFee = 2^63` (just above `math.MaxInt64`), `ElasticityMultiplier = 2`, `BaseFeeChangeDenominator = 8` (defaults):

```
baseFee.Int64()  →  math.MinInt64  (= -9223372036854775808, silent truncation)
maxDelta = math.MinInt64 * (2-1) / 8
         = -9223372036854775808 / 8
         = -1152921504606846976   (negative)
→ clamped to 0
```

`eth_maxPriorityFeePerGas` returns `0x0` instead of the correct `~1.15 × 10^18 wei`.

For the multiplication overflow path, assume `baseFee = 5 × 10^18` (fits in int64), `ElasticityMultiplier = 3`:

```
baseFee.Int64() = 5000000000000000000
(ElasticityMultiplier - 1) = 2
product = 5000000000000000000 * 2 = 10000000000000000000
math.MaxInt64              =  9223372036854775807
overflow → wraps to: 10000000000000000000 - 2^64 = -8446744073709551616  (negative)
→ clamped to 0
```

Again returns 0. For a slightly different multiplier that causes the product to wrap positive, the returned value would be an arbitrary large incorrect number fed directly into `eth_gasPrice` and `SetTxDefaults`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** rpc/namespaces/ethereum/eth/api.go (L371-382)
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
}
```

**File:** rpc/backend/call_tx.go (L232-245)
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
```

**File:** rpc/backend/call_tx.go (L468-498)
```go
// GasPrice returns the current gas price based on Ethermint's gas price oracle.
func (b *Backend) GasPrice() (*hexutil.Big, error) {
	var (
		result *big.Int
		err    error
	)
	head, err := b.CurrentHeader()
	if err != nil {
		return nil, err
	}
	if head.BaseFee != nil {
		result, err = b.SuggestGasTipCap(head.BaseFee)
		if err != nil {
			return nil, err
		}
		result = result.Add(result, head.BaseFee)
	} else {
		result = b.RPCMinGasPrice()
	}

	// return at least GlobalMinGasPrice from FeeMarket module
	minGasPrice, err := b.GlobalMinGasPrice()
	if err != nil {
		return nil, err
	}
	minGasPriceInt := minGasPrice.TruncateInt().BigInt()
	if result.Cmp(minGasPriceInt) < 0 {
		result = minGasPriceInt
	}

	return (*hexutil.Big)(result), nil
```
