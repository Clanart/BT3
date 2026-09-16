### Title
Gasless swap-for-gas repayment relies solely on a single unprotected AMM spot price (no oracle/TWAP), allowing manipulation of the token-to-KAIA exchange rate used to reimburse block proposers - (File: `kaiax/gasless/impl/tx_pool.go`, `kaiax/gasless/impl/getter.go`)

### Summary
The gasless module lets an unprivileged, gas-less sender submit an `Approve`+`SwapForGas` transaction pair. A block proposer advances real KAIA to the sender via a `LendTx` and expects to be repaid a fixed KAIA amount by swapping the sender's ERC-20 token for KAIA through a single DEX pool referenced by `GaslessSwapRouter`. The only "price feed" validating that the token amount offered is worth the KAIA being advanced is the live spot price of one AMM pool (`getReserves`/constant-product `getAmountIn`), with no Chainlink-style oracle, TWAP, or price-deviation bound — the exact bug class flagged in the referenced report ("don't trust a single, manipulable price source for settlement").

### Finding Description
`GaslessModule.checkBalanceForSwap` is the only economic safety check gating a `SwapForGas` transaction into the pool. It verifies `tx.amountIn >= gsr.getAmountIn(token, minAmountOut)`, where `getAmountIn` is a call into `GaslessSwapRouter`, which is implemented over a Uniswap-V2-style pair (see the reserve-based `getAmountIn`/`getAmountOut`/`getReserves` bindings in `contracts/bindings/uniswap/...` and `GaslessSwapRouter.GetAmountIn`). [1](#0-0) 

This spot price is a single pool's instantaneous reserve ratio; there is no fallback, sanity bound, or independent price reference. Meanwhile, the amount the proposer is owed (`amountRepay`/`lendAmount`) is computed purely from raw KAIA gas-fee arithmetic, independent of any real value peg for the swapped token: [2](#0-1) 

The proposer's `LendTx` advances KAIA to the sender before the swap settles the debt: [3](#0-2) 

Because `minAmountOut` is fully attacker-chosen (bounded only by `>= amountRepay`) and the sole "amountIn sufficiency" gate is derived from the same manipulable pool that will execute the real swap, a sender who also controls (or can influence, e.g., via a preceding swap in the same block/bundle) the pool's reserves can transiently skew the reserve ratio so that a minimal `amountIn` of a devalued/manipulated token satisfies `getAmountIn(minAmountOut)` at admission time, while the deposited/whitelisted token is not actually worth the KAIA already advanced by the proposer. This mirrors the audited Unitas issue: trusting a single on-chain price instead of an external oracle (e.g., Chainlink) for a value peg used in settlement.

### Impact Explanation
If exploited, the block proposer's advanced KAIA (`lendAmount`, sent via `LendTx`) can be under-collateralized by the token actually received through `swapForGas`, since the only gatekeeping mechanism is the same single AMM's spot price that is being manipulated. This results in direct unauthorized value extraction from the block proposer/fee-delegation counterparty — a fee-delegation and settlement abuse reachable by any unprivileged gasless transaction sender, matching the Medium-severity "manipulable price used for settlement" bug class from the report.

### Likelihood Explanation
Any account can submit the gasless `Approve`+`SwapForGas` bundle; no special privilege is required (`kaiax/gasless/impl/getter.go` `IsApproveTx`/`IsSwapTx`/`IsExecutable`). The exchange-rate constraint is evaluated exclusively against one DEX pool's live reserves without a TWAP window or deviation cap, so an attacker with the ability to move that pool's price for even a single block (which is realistic for low-liquidity whitelisted token pools) can affect both the admission check and the actual swap execution, since they use the same underlying spot price.

### Recommendation
Do not rely solely on the single AMM pool's instantaneous reserve ratio (`getReserves`/`getAmountIn`) to authorize gasless-swap repayment amounts. Introduce an independent, manipulation-resistant reference price (e.g., a TWAP over multiple blocks, or an external oracle) and bound the acceptable deviation between the DEX spot price and that reference before allowing `checkBalanceForSwap` to admit a `SwapForGas` transaction, and/or require the swap execution itself to validate against the same bounded reference price rather than trusting the live pool alone.

### Proof of Concept
1. Governance whitelists token `T` with a DEX pool `T/WKAIA` that has thin liquidity.
2. Attacker submits (or bundles) a large swap that skews the `T/WKAIA` reserve ratio in the same block/state used for pool admission.
3. Attacker crafts `SwapForGas(token=T, amountIn=X, minAmountOut=Y, amountRepay=Y, deadline)` where `X` is minimal, computed to satisfy `X >= GaslessSwapRouter.getAmountIn(T, Y)` under the skewed reserves (`kaiax/gasless/impl/tx_pool.go:128-141`).
4. The proposer's `LendTx` (computed via `lendAmount`, `kaiax/gasless/impl/getter.go:346-359`) has already sent KAIA to the attacker's account.
5. The `SwapForGas` executes against the same manipulated pool, and because the check and execution both trust only this single spot price, the proposer ends up under-repaid relative to the KAIA it fronted, with no oracle-based safeguard to detect the discrepancy.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L128-141)
```go
	if g.GaslessConfig.ShouldCheckSwapAmount() {
		// tx.amountIn >= gsr.getAmountIn(minAmountOut)
		routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
		if err != nil {
			return err
		}
		// Required token amountIn, given the current exchange rate and the declared minAmountOut.
		requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
		if err != nil {
			return err
		}
		if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
			return fmt.Errorf("insufficient amountIn: have=%s, want=%s", swapArgs.AmountIn.String(), requiredAmountIn.String())
		}
```

**File:** kaiax/gasless/impl/getter.go (L268-313)
```go
// MakeLendTx creates a transaction with following properties:
// L1. LendTx.type = 0x7802 (TxTypeEthereumDynamicFee)
// L2. LendTx.from = proposer
// L3. LendTx.to = SwapTx.from
// L4. LendTx.value = LendAmount(approveTxOrNil, swapTx)
func (g *GaslessModule) GetLendTxGenerator(approveTxOrNil, swapTx *types.Transaction) *builder.TxOrGen {
	var src []byte
	if approveTxOrNil != nil {
		src = append(src, approveTxOrNil.Hash().Bytes()...)
	}
	src = append(src, swapTx.Hash().Bytes()...)
	bundleHash := crypto.Keccak256Hash(src)

	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId = g.InitOpts.ChainConfig.ChainID
			signer  = types.LatestSignerForChainID(chainId)
			key     = g.InitOpts.NodeKey
		)

		to, err := types.Sender(signer, swapTx)
		if err != nil {
			return nil, err
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &to,
			types.TxValueKeyAmount:     lendAmount(approveTxOrNil, swapTx),
			types.TxValueKeyData:       common.Hex2Bytes("0x"),
			types.TxValueKeyGasLimit:   params.TxGas,
			types.TxValueKeyGasFeeCap:  swapTx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  swapTx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)
		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bundleHash)
}
```

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```
