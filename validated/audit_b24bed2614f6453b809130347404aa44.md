### Title
Gasless swap `repayAmount` overcharges users because it uses `GasFeeCap` instead of the actual effective gas price of the LendTx - (File: kaiax/gasless/impl/getter.go)

### Summary
The gasless module (KIP-247) computes how much a gasless-swap user must repay the proposer for the gas the proposer fronted (the "LendTx"). The repay formula uses `swapTx.GasPrice()` to estimate the LendTx's real cost, but for EIP-1559-style transactions `GasPrice()` returns the `GasFeeCap` (the price ceiling), not the actually-paid effective gas price. This mirrors the reported bug class: a value is computed using the wrong "unit"/quantity (fee cap instead of effective/execution price), producing a systematically incorrect price/amount calculation.

### Finding Description
`Transaction.GasPrice()` is a generic accessor that, for a dynamic-fee (`TxTypeEthereumDynamicFee`) transaction, returns `t.data.GetGasPrice()` which is implemented as the tx's `GasFeeCap`: [1](#0-0) [2](#0-1) 

However, the *actual* fee a dynamic-fee transaction pays on execution is the effective gas price, `min(tipCap, feeCap-baseFee) + baseFee`, computed by `EffectiveGasTip`/`EffectiveGasPrice`, and it is this effective price (not `GasFeeCap`) that is deducted from the payer's balance during `buyGas`/`returnGas` in state transition: [3](#0-2) [4](#0-3) 

In the gasless module, `GetLendTxGenerator` builds the "LendTx" (the proposer-funded transaction that fronts gas for the user) as a dynamic-fee transaction whose `GasFeeCap`/`GasTipCap` are copied from the swap transaction: [5](#0-4) 

The amount the user must repay for this LendTx ("R1") is computed as: [6](#0-5) 

```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

The comment explicitly (and incorrectly) asserts `LendTx.Fee() == SwapTx.GasPrice() * TxGas`. But `swapTx.GasPrice()` is the `GasFeeCap`, while `LendTx.Fee()` — the actual amount deducted from the proposer's balance when the LendTx is included in the block — is `EffectiveGasPrice(header, config) * gasUsed`, which is generally strictly lower than `GasFeeCap * TxGas` whenever `baseFee + tipCap < feeCap` (the normal EIP-1559 case where a fee cap includes headroom above the current base fee). Since the block being built already has a known `baseFee`, the proposer can compute the true effective price at bundle-construction time, so using `GasFeeCap` instead is an avoidable unit/quantity mismatch identical in nature to the reported `StableOracleDAI` bug (mixing a "ceiling" value where an "actual/effective" value belongs).

This computed `repayAmount` is enforced as a mandatory equality check (`SP4`) against the user-supplied `AmountRepay` field of the swap call for the transaction to be treated as a valid/executable gasless transaction: [7](#0-6) 

Since `AmountRepay` (transferred back to the lender/proposer through the on-chain gasless-swap flow) must exactly equal `repayAmount()`, every gasless-swap user is forced to repay `(GasFeeCap - EffectiveGasPrice) * TxGas` more than what the proposer actually spent on the LendTx, with the surplus flowing to the proposer.

### Impact Explanation
This is a direct, unauthorized value-movement / fee-delegation abuse: an unprivileged public RPC caller (any gasless-swap user reachable via `auction_submitBid`-adjacent `gasless` flow / normal transaction submission) is systematically overcharged on every gasless swap whenever the swap tx's `maxFeePerGas` exceeds the effective/execution gas price of the block it lands in — which is the common case since users typically set feeCaps with headroom over the current base fee. The excess is captured by the block proposer, i.e. value is redirected from the gasless-tx sender to the proposer beyond what is economically justified (proposer's actual out-of-pocket LendTx cost). Because the mismatch scales with `TxGas` (21000) times the feeCap–effective-price gap, and applies to every processed gasless swap, this constitutes a per-transaction reward/value skew that satisfies the "gasless…settlement theft" / "fee delegation abuse" category called out in the validation rules.

### Likelihood Explanation
High likelihood of triggering on virtually every real-world gasless swap: EIP-1559 clients conventionally set `maxFeePerGas` well above the current base fee to tolerate base-fee volatility, so `GasFeeCap > EffectiveGasPrice` is the normal/expected condition, not an edge case. No malicious actor coordination or special conditions are required — any ordinary gasless swap sender using KIP-247 triggers the overcharge as an inherent side effect of the formula, and the block proposer benefits from processing gasless transactions.

### Recommendation
Compute R1 using the LendTx's actual effective gas price at the time of block building (i.e., `swapTx.EffectiveGasPrice(header, chainConfig)` or an equivalent that accounts for the block's baseFee and the swap tx's tip/fee cap), rather than `swapTx.GasPrice()` (which resolves to the fee cap for dynamic-fee transactions). For example:
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction, header *types.Header, config *params.ChainConfig) *big.Int {
    effectivePrice := swapTx.EffectiveGasPrice(header, config)
    r1 := new(big.Int).Mul(effectivePrice, new(big.Int).SetUint64(params.TxGas))
    return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```
Ensure the same effective-price basis is used consistently when `VerifyExecutable`/`IsExecutable` validates `AmountRepay`, and when the LendTx is actually generated/signed, so the user is charged exactly the proposer's real cost.

### Proof of Concept
1. Deploy/observe a network state where the current block's `baseFee` is, say, 25 gkei.
2. A gasless-swap user submits `GaslessSwapTx` with `GasFeeCap = 750 gkei`, `GasTipCap = 5 gkei` (a typical safety-margin fee cap), and correctly computes `AmountRepay` per the KIP-247/README formula, i.e. equal to `repayAmount(...)` as implemented — which uses `GasFeeCap` (750 gkei) × 21000 for R1.
3. When the proposer builds the block via `GetLendTxGenerator`, the LendTx it actually broadcasts has `GasFeeCap=750gkei, GasTipCap=5gkei`; its real, on-chain-deducted fee (per `buyGas`/`EffectiveGasPrice`) is `min(5, 750-25)+25 = 30 gkei` × 21000 — 25x cheaper than the 750 gkei × 21000 baked into `AmountRepay`.
4. The user's swap therefore repays the proposer 750gkei×21000 while the proposer's true LendTx cost was only 30gkei×21000, pocketing the ~720gkei×21000 difference — repeated on every gasless swap that follows normal EIP-1559 fee-cap conventions.

(Note: the exact on-chain mechanics of how `AmountRepay` is transferred to the proposer/lender live in the `GaslessSwapRouter` Solidity contract, which was not directly inspected in this pass; the Go-side formula mismatch and its consumption as a strict equality check in `VerifyExecutable` were confirmed directly from source.)

### Citations

**File:** blockchain/types/tx_internal_data_ethereum_dynamic_fee.go (L211-213)
```go
func (t *TxInternalDataEthereumDynamicFee) GetGasPrice() *big.Int {
	return t.GasFeeCap
}
```

**File:** blockchain/types/transaction.go (L300-316)
```go
func (tx *Transaction) Gas() uint64        { return tx.data.GetGasLimit() }
func (tx *Transaction) GasPrice() *big.Int { return new(big.Int).Set(tx.data.GetGasPrice()) }
func (tx *Transaction) GasTipCap() *big.Int {
	if te, ok := tx.GetTxInternalData().(TxInternalDataBaseFee); ok {
		return te.GetGasTipCap()
	}

	return tx.data.GetGasPrice()
}

func (tx *Transaction) GasFeeCap() *big.Int {
	if te, ok := tx.GetTxInternalData().(TxInternalDataBaseFee); ok {
		return te.GetGasFeeCap()
	}

	return tx.data.GetGasPrice()
}
```

**File:** blockchain/types/transaction.go (L318-339)
```go
func (tx *Transaction) EffectiveGasTip(baseFee *big.Int) *big.Int {
	// effectiveGasPrice - baseFee = min(baseFee + tipCap, feeCap) - baseFee = min(tipCap, feeCap - baseFee)
	if baseFee != nil {
		// For EthereumDynamicFee TxType: min(GasTipCap, Sub(GasFeeCap,baseFee))
		// For Non-EthereumDynamicFee TxType: min(GasPrice, Sub(gasPrice, baseFee)
		tip := math.BigMax(big.NewInt(0), new(big.Int).Sub(tx.GasFeeCap(), baseFee))
		return math.BigMin(tx.GasTipCap(), tip)
	}

	return new(big.Int).Set(tx.GasTipCap())
}

func (tx *Transaction) EffectiveGasPrice(header *Header, config *params.ChainConfig) *big.Int {
	if header == nil || header.BaseFee == nil {
		return new(big.Int).Set(tx.GasPrice())
	}
	if config.Rules(header.Number).IsKaia {
		tip := tx.EffectiveGasTip(header.BaseFee)
		return new(big.Int).Add(tip, header.BaseFee)
	}
	return new(big.Int).Set(header.BaseFee)
}
```

**File:** blockchain/state_transition.go (L294-308)
```go
func (st *StateTransition) buyGas() error {
	var (
		validatedFeePayer = st.msg.ValidatedFeePayer()
		validatedSender   = st.msg.ValidatedSender()
		feeRatio, _       = st.msg.FeeRatio()
		isOsaka           = st.evm.ChainConfig().Rules(st.evm.Context.BlockNumber).IsOsaka
	)

	// mgval is the maximum gas fee that can actually be paid in the worst case (e.g., revert)
	// st.gasPrice = tx.gasPrice (before Magma) or effectiveGasPrice (since Magma)
	mgval := new(big.Int).Mul(new(big.Int).SetUint64(st.msg.Gas()), st.gasPrice)

	// feeCap is the maximum gas fee the sender was willing to pay
	// GasFeeCap = tx.maxFeePerGas (if exists) or tx.gasPrice
	feeCap := new(big.Int).Mul(new(big.Int).SetUint64(st.msg.Gas()), st.msg.GasFeeCap())
```

**File:** kaiax/gasless/impl/getter.go (L260-266)
```go
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
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

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```
