### Title
Gasless module trusts the Registry-configured GaslessSwapRouter address by identity comparison only, with no interface/behavior verification - (File: kaiax/gasless/impl/getter.go)

### Summary
The gasless tx-pool/execution logic reads the `GaslessSwapRouter` address from the on-chain `Registry` system contract (via `MultiCallGaslessInfo`) and caches it in `g.swapRouter`. Every subsequent admission/execution check (`isApproveTx`, `isSwapTx`, `checkBalanceForSwap`) merely compares `tx.To()`/`spender` against this cached address, and blindly ABI-calls `GetAmountIn`/`swapForGas` on it. There is no EIP-165 or any other interface/behavior verification analogous to the one recommended in the reported `UXDController.updateRouter`/`setRedeemable` issue, where an address is accepted as "the router" only because it is *equal to a configured value*, not because it is verified to *behave like* a swap router.

### Finding Description
`updateAddresses` fetches the router address from the Registry every block and stores it with no validation beyond the RPC call succeeding: [1](#0-0) 

The admission checks (`isApproveTx`/`isSwapTx`) only compare addresses, never confirming the target contract actually implements the expected `GaslessSwapRouter` interface: [2](#0-1) 

Mempool-level balance validation trusts the router's `GetAmountIn` return value directly, with no sanity bound or interface check, to gate whether a swap tx is admitted: [3](#0-2) 

Crucially, before the swap itself is executed, the proposer node signs and injects a **real value-transfer transaction** (`LendTx`) that sends native KAIA to the swap sender, sized by `LendAmount`/`RepayAmount`, which are computed purely from the swap tx's own declared fields (`GasPrice`, `Fee()`), not from any verified router behavior: [4](#0-3) [5](#0-4) 

The only guard on `AmountRepay` is a numeric equality check against the locally computed expected repay amount (`VerifyExecutable`, SP4), not any confirmation that the router contract at `g.swapRouter` will actually perform a correct swap and forward `amountRepay` back: [6](#0-5) 

This mirrors the reported bug class exactly: an externally-configured address (here, Registry-set `GaslessSwapRouterName`) is consumed by security-critical logic (fee-delegation/lending decisions) using only an identity/existence check, never an interface-conformance check that the target actually implements the expected router semantics.

### Impact Explanation
Because the module lends real KAIA to the gasless-tx sender *before* the swap executes, and the only backstop is that `swapTx.to == g.swapRouter`, any misregistration or misbehaving contract at the `GaslessSwapRouterName` Registry slot (whether due to a governance mistake, an upgrade to a router with different semantics, or a contract that doesn't fully implement the intended repay flow) breaks the fee-delegation guarantee. The lending amount is computed independently of whether the router will honor `amountRepay`; if the router fails to transfer the repay amount back to the fee payer (e.g. reverts partially, has a different fee schedule, or doesn't actually call `transferFrom` for the declared `amountRepay`), the protocol/proposer's lent KAIA is not recovered, resulting in unauthorized value loss/fee-delegation abuse reachable by any gasless-swap submitter.

### Likelihood Explanation
This is reachable by any ordinary user submitting an approve+swap transaction bundle recognized by `IsApproveTx`/`IsSwapTx` — no special privilege is needed to trigger the flawed check once the router address is in place, matching the "gasless module" and "fee-delegation" reachability criteria. The triggering precondition (an incorrect/behaviorally-nonconforming address at the `GaslessSwapRouterName` Registry slot) requires either a governance misconfiguration or a router upgrade with subtly different repay semantics — this is analogous to the original report's scenario where governance-set contract addresses are trusted without interface verification.

### Recommendation
Add explicit interface/behavior verification when consuming the Registry-provided router address in `updateAddresses`:
- Verify the address has code and, ideally, that it implements the expected `GaslessSwapRouter` interface (e.g., via EIP-165 `supportsInterface`, or by requiring a governance-controlled allow-list rather than reading directly from a mutable Registry slot).
- Do not rely solely on `tx.To() == g.swapRouter` for admission; consider requiring the actual `swapForGas` call to atomically transfer `amountRepay` within the same transaction/bundle, and add post-execution verification that the fee payer was actually repaid before finalizing lending, rather than trusting the router's declared behavior implicitly.

### Proof of Concept
1. Governance (or an upgrade process) registers a new address `R'` for `GaslessSwapRouterName` in the Registry system contract that does not correctly implement `swapForGas`'s repay logic (e.g., it forwards less than `amountRepay`, or omits the repay transfer entirely due to a bug).
2. At the next block, `PostInsertBlock` → `updateAddresses` reads this address via `getGaslessInfo`/`MultiCallGaslessInfo` and sets `g.swapRouter = R'` with no interface check: [7](#0-6) 
3. Any user submits a standard approve+swap gasless bundle targeting `R'`; `isApproveTx`/`isSwapTx` accept it purely because `R' == g.swapRouter`: [2](#0-1) 
4. The proposer computes `LendAmount`/`RepayAmount` from the swap tx fields alone and sends the `LendTx` (real KAIA transfer) to the sender before/alongside the swap: [8](#0-7) 
5. Because `R'` doesn't correctly repay, the lent KAIA is not recovered, resulting in a net loss of funds from the lending party (proposer/protocol) despite passing all of the module's checks — none of which verify the router's actual interface/behavior.

### Citations

**File:** kaiax/gasless/impl/getter.go (L79-103)
```go
func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
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

**File:** kaiax/gasless/impl/getter.go (L315-329)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

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
