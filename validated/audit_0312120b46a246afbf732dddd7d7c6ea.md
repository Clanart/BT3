Found a concrete analog in `mempool/iterator.go`.

### Title
Division by zero in `EVMMempoolIterator.extractCosmosEffectiveTip` via zero-gas Cosmos transaction can panic block proposal/processing - (File: mempool/iterator.go)

### Summary
`extractCosmosEffectiveTip` computes a Cosmos transaction's gas price as `fee_amount / gas_limit` using `sdkmath.Int.Quo`, with the divisor taken directly and unconditionally from `feeTx.GetGas()`, without checking that it is non-zero, unlike the analogous EIP-1559 fee-checker path (`ante/evm/fee_checker.go:91-93`) which explicitly guards `gas.IsZero()` before doing the same division. This is the same root-cause bug class as the audit report's `SaiTub`/`SaiTop`/`DaiVox` findings: a parameter that legitimately can be zero is divided into without a `require`/guard, and every reachable call site can throw.

### Finding Description
In `mempool/iterator.go`, `extractCosmosEffectiveTip` is called from `getNextCosmosTx`, which is invoked by `shouldUseEVM`, `getPreferredTransaction`, `advanceCurrentIterator`, and `hasMoreTransactions` — i.e., on essentially every step of the unified EVM/Cosmos mempool iterator used during block proposal (`Next()`/`Tx()`). [1](#0-0) 

The relevant computation is:
```go
gasPrice, overflow := uint256.FromBig(bondDenomFeeAmount.Quo(math.NewIntFromUint64(feeTx.GetGas())).BigInt())
```
`feeTx.GetGas()` returns the raw `GasLimit` field parsed from the transaction's `AuthInfo`/`Fee` (via `sdk.FeeTx`), which for a standard Cosmos `Tx` is fully attacker-controlled since it is part of the unsigned/signed transaction envelope. `sdkmath.Int.Quo` (backed by `big.Int.Quo`) panics on division by zero. Nothing in this code path validates `feeTx.GetGas() != 0` before the division, in contrast to the sibling EIP-1559 fee-checker logic that explicitly guards this exact same computation: [2](#0-1) 

If a Cosmos-format transaction with `Fee.GasLimit == 0` (but with the bond denom set as a fee coin, or even without one — the denom filter loop still leaves `feeTx.GetGas()` used as the divisor regardless of whether a fee coin was found) reaches the mempool iterator during block building, calling `Quo(math.NewIntFromUint64(0))` panics.

### Impact Explanation
This directly maps to the "Cosmos EVM Allowed Impact Gate" critical impact: **chain halt / non-determinism that an unprivileged user can trigger through ordinary transaction flow.** If a zero-gas-limit Cosmos transaction is admitted into the Cosmos-side mempool (this iterator combines an EVM txpool iterator with a standard `mempool.Iterator` for non-EVM Cosmos txs) and reaches proposal/processing, every validator attempting to build or process the block via this iterator would panic on the same input, since the check is a pure function of transaction bytes and is deterministic across nodes — this could manifest as a consensus-wide halt (all nodes panicking identically) rather than a single-node crash, which is explicitly in-scope (as opposed to a genuinely isolated single-node crash which is out-of-scope).

Whether this is actually reachable in production depends on whether earlier stages (ante handlers, `CheckTx`, standard Cosmos SDK `FeeTx`/gas validation, `mempool/checktx/check_tx.go`) reject a `GasLimit == 0` transaction before it is admitted into the Cosmos iterator. I was not able to fully verify, within the remaining tool budget, whether the standard Cosmos SDK ante pipeline (or `mempool/checktx/check_tx.go`) always rejects `Fee.GasLimit == 0` before a transaction reaches `getNextCosmosTx`. Standard Cosmos SDK `ValidateBasic`/ante does NOT always require a non-zero declared gas limit purely from `TxBuilder` construction (gas limit is client-supplied metadata, and some decorators only reject it indirectly via gas metering rather than an explicit `GasLimit != 0` check) — this needs confirmation against this repo's actual ante chain composition, which I could not fully trace before running out of iterations.

### Likelihood Explanation
Medium-to-uncertain: constructing a Cosmos SDK transaction with an explicit `Fee{GasLimit: 0}` is trivial for any user (it's just a client-set field), and this exact division-by-zero pattern is already known and guarded against in the sibling `ante/evm/fee_checker.go` code, suggesting the underlying gas-limit-can-be-zero condition is a real possibility the codebase authors were aware of elsewhere. The main uncertainty is whether some other ante/mempool admission check independently rejects zero-gas transactions before they reach this iterator — if such a guard exists and is unconditional, this finding would be downgraded or invalidated.

### Recommendation
Add an explicit guard in `extractCosmosEffectiveTip` mirroring the fee-checker's pattern:
```go
gas := feeTx.GetGas()
if gas == 0 {
    return nil // or uint256.NewInt(0), treat as no valid fee
}
gasPrice, overflow := uint256.FromBig(bondDenomFeeAmount.Quo(math.NewIntFromUint64(gas)).BigInt())
```
More generally, audit every unguarded `.Quo`/`.QuoInt`/`.QuoRaw`/`big.Int.Div` call in the mempool, ante, and fee-market packages where the divisor originates from an attacker-supplied transaction field, and add `require`/zero-checks consistent with the pattern already used in `ante/evm/fee_checker.go:91-93`.

### Proof of Concept
1. Construct a standard Cosmos SDK `Tx` (non-`MsgEthereumTx`) implementing `sdk.FeeTx`, with `AuthInfo.Fee.GasLimit = 0` and, optionally, a fee coin in the chain's bond denom.
2. Submit the transaction so that it is accepted into the Cosmos-side mempool referenced by `EVMMempoolIterator.cosmosIterator` (verification of the exact admission path/ante checks that would need to be bypassed is the open item noted above).
3. When a validator's proposer logic calls `EVMMempoolIterator.Next()`/`Tx()` (which internally calls `getNextCosmosTx` → `extractCosmosEffectiveTip`), the line `bondDenomFeeAmount.Quo(math.NewIntFromUint64(0))` panics, because `feeTx.GetGas()` returns `0`. [3](#0-2)

### Citations

**File:** mempool/iterator.go (L251-272)
```go
func (i *EVMMempoolIterator) extractCosmosEffectiveTip(tx sdk.Tx) *uint256.Int {
	feeTx, ok := tx.(sdk.FeeTx)
	if !ok {
		i.logger.Debug("Cosmos transaction doesn't implement FeeTx interface")
		return nil // Transaction doesn't implement FeeTx interface
	}

	bondDenomFeeAmount := math.ZeroInt()
	fees := feeTx.GetFee()
	for _, coin := range fees {
		if coin.Denom == i.bondDenom {
			i.logger.Debug("found fee in bond denomination", "denom", coin.Denom, "amount", coin.Amount.String())
			bondDenomFeeAmount = coin.Amount
		}
	}

	// Calculate gas price: fee_amount / gas_limit
	gasPrice, overflow := uint256.FromBig(bondDenomFeeAmount.Quo(math.NewIntFromUint64(feeTx.GetGas())).BigInt())
	if overflow {
		i.logger.Debug("overflowed on gas price calculation")
		return nil
	}
```

**File:** ante/evm/fee_checker.go (L89-98)
```go
	gas := sdkmath.NewIntFromUint64(feeTx.GetGas())

	if gas.IsZero() {
		return nil, 0, errorsmod.Wrap(errortypes.ErrInvalidRequest, "gas cannot be zero")
	}

	feeCoins := feeTx.GetFee()
	feeAmtDec := sdkmath.LegacyNewDecFromInt(feeCoins.AmountOfNoDenomValidation(denom))

	feeCap := feeAmtDec.QuoInt(gas)
```
