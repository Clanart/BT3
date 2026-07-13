### Title
`baseFee.Int64()` Silent Truncation in `SuggestGasTipCap` Produces Incorrect `eth_gasPrice` / `eth_maxPriorityFeePerGas` When Base Fee Exceeds `math.MaxInt64` - (File: `rpc/backend/chain_info.go`)

---

### Summary

`SuggestGasTipCap` in `rpc/backend/chain_info.go` casts the `*big.Int` base fee to `int64` via `.Int64()` before performing arithmetic. Because the EIP-1559 base fee is an arbitrary-precision integer that can grow without bound, any base fee value above `math.MaxInt64` (~9.2 × 10¹⁸ wei) causes silent truncation. The result is that `eth_gasPrice`, `eth_maxPriorityFeePerGas`, and `SetTxDefaults` all return a tip cap of `0` instead of the correct positive value, feeding incorrect fee data into every transaction constructed through the JSON-RPC layer.

---

### Finding Description

In `SuggestGasTipCap`, the maximum base-fee delta is computed as:

```go
maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)
``` [1](#0-0) 

`baseFee` is a `*big.Int` sourced from the feemarket module's `Params.BaseFee`, which is itself an `sdkmath.Int` (arbitrary precision). The EIP-1559 base fee increases by up to 12.5 % per block when blocks are consistently full; after enough full blocks it will exceed `math.MaxInt64`.

Go's `big.Int.Int64()` returns only the low 64 bits interpreted as a signed integer — it does **not** panic or return an error. For any `baseFee` in the range `(math.MaxInt64, math.MaxUint64]` the high bit is set, so `.Int64()` returns a negative value. The subsequent multiplication produces a negative `maxDelta`, which the guard `if maxDelta < 0 { maxDelta = 0 }` silently clamps to zero. For `baseFee > math.MaxUint64` the low 64 bits may be a small positive value, yielding a wildly underestimated tip.

The function is called from three public JSON-RPC paths:

1. `eth_gasPrice` → `GasPrice()` → `SuggestGasTipCap()` [2](#0-1) 
2. `eth_maxPriorityFeePerGas` → `SuggestGasTipCap()` [3](#0-2) 
3. `eth_sendTransaction` → `SetTxDefaults()` → `SuggestGasTipCap()` [4](#0-3) 

The `baseFee` stored in feemarket params is an `sdkmath.Int` with no upper-bound cap enforced at the type level. [5](#0-4) 

The analogous safe pattern used elsewhere in the codebase is `ethermint.SafeInt64(value)`, which returns an error instead of silently truncating. [6](#0-5) 

---

### Impact Explanation

When `baseFee > math.MaxInt64`:

- `eth_maxPriorityFeePerGas` returns `0` instead of the correct positive tip cap.
- `eth_gasPrice` returns `baseFee + 0 = baseFee` — no tip component.
- `SetTxDefaults` sets `MaxPriorityFeePerGas = 0` and `MaxFeePerGas = 2 × baseFee` for every transaction built through `eth_sendTransaction`.

Transactions constructed with `MaxPriorityFeePerGas = 0` carry no validator tip. If the base fee continues to rise (as it will when blocks are full), `MaxFeePerGas = 2 × baseFee` may quickly become insufficient, causing all RPC-constructed transactions to be rejected by the ante handler's `GasFeeCap < baseFee` check. This is a fee market / ante handler bug that causes valid user transactions to be mis-accounted and potentially stuck, matching the allowed High impact: *"Public JSON-RPC path feeds incorrect consensus-critical data into transaction execution."*

---

### Likelihood Explanation

The EIP-1559 base fee grows by up to 12.5 % per block when blocks are full. Starting from the Ethereum mainnet default of 1 Gwei (10⁹ wei), reaching `math.MaxInt64` (~9.2 × 10¹⁸ wei) requires sustained full blocks, but on a chain with a low initial base fee or aggressive elasticity parameters this threshold is reachable. Any operator who sets `InitialBaseFee` close to `math.MaxInt64` in genesis, or any chain that has been running at full capacity for an extended period, will trigger this path. No special privileges are required — the attacker simply submits enough transactions to keep blocks full.

---

### Recommendation

Replace the unsafe `baseFee.Int64()` cast with big-integer arithmetic throughout `SuggestGasTipCap`:

```go
// Before (unsafe):
maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)

// After (safe):
elasticity := new(big.Int).SetInt64(int64(params.Params.ElasticityMultiplier) - 1)
denominator := new(big.Int).SetInt64(int64(params.Params.BaseFeeChangeDenominator))
maxDeltaBig := new(big.Int).Mul(baseFee, elasticity)
maxDeltaBig.Div(maxDeltaBig, denominator)
if maxDeltaBig.Sign() < 0 {
    maxDeltaBig = new(big.Int)
}
return maxDeltaBig, nil
```

Also audit `x/feemarket/keeper/abci.go` for the same `baseFee.Int64()` pattern identified by grep.

---

### Proof of Concept

1. Deploy an Ethermint chain with `InitialBaseFee = math.MaxInt64 + 1` (e.g., `9223372036854775808`), or allow the base fee to grow naturally to that level by keeping blocks full.
2. Call `eth_maxPriorityFeePerGas` via JSON-RPC.
3. Observe the response is `0x0` instead of a positive tip cap.
4. Call `eth_gasPrice`; observe it returns exactly `baseFee` with no tip component.
5. Submit a transaction via `eth_sendTransaction` without explicit fee fields; `SetTxDefaults` will set `MaxPriorityFeePerGas = 0` and `MaxFeePerGas = 2 × baseFee`. As the base fee continues to rise, `MaxFeePerGas` will fall below the new `baseFee`, and the ante handler will reject the transaction with `"the tx gasfeecap is lower than the tx baseFee"`.

### Citations

**File:** rpc/backend/chain_info.go (L439-444)
```go
	maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)
	if maxDelta < 0 {
		// impossible if the parameter validation passed.
		maxDelta = 0
	}
	return big.NewInt(maxDelta), nil
```

**File:** rpc/backend/call_tx.go (L232-246)
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
```

**File:** rpc/backend/call_tx.go (L478-484)
```go
	if head.BaseFee != nil {
		result, err = b.SuggestGasTipCap(head.BaseFee)
		if err != nil {
			return nil, err
		}
		result = result.Add(result, head.BaseFee)
	} else {
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

**File:** x/feemarket/keeper/eip1559.go (L52-55)
```go
	parentBaseFee := params.BaseFee.BigInt()
	if parentBaseFee == nil {
		return nil
	}
```

**File:** types/int.go (L54-61)
```go
// SafeInt64 checks for overflows while casting a uint64 to int64 value.
func SafeInt64(value uint64) (int64, error) {
	if value > uint64(math.MaxInt64) {
		return 0, fmt.Errorf("uint64 value %v cannot exceed %v", value, math.MaxInt64)
	}

	return int64(value), nil
}
```
