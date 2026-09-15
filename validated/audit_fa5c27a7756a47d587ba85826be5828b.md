## Finding: Gasless swap settlement relies on unprotected spot AMM price, enabling single-block price-manipulation theft from the gasless liquidity pool

The HYDT report describes a classic DeFi bug class: a protocol computes a mint/swap amount from the *instantaneous* reserves of a public AMM pair (no TWAP, no deviation bound), so an attacker who moves the reserves within the same transaction/block (via flash loan or an ordinary large swap) can force the protocol to use an attacker-favorable price and extract value.

Kaia's `kaiax/gasless` module (KIP-247) reproduces this exact pattern for gas-fee sponsorship, and it is reachable by any unprivileged gasless-tx sender:

- `GaslessSwapRouter.getAmountIn`/`getAmountsOut` (bound in `contracts/bindings/kip247/GaslessSwapRouter.go`) is a live, spot-price view into whatever DEX pair is registered for the token (`getDEXInfo`), with no staleness or deviation protection. [1](#0-0) [2](#0-1) 

- The Kaia node itself uses this same live-price call, `routerContract.GetAmountIn(nil, token, minAmountOut)`, to gate admission of a `GaslessSwapTx` into the pool/block, and the actual `swapForGas` execution likewise resolves its output from the same live pair at block-execution time: [3](#0-2) 

- The gasless bundle (`[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`) is inserted at the position of the original `GaslessSwapTx` in the block, so any transaction ordered earlier in the same block — including one submitted by the gasless sender themselves — executes and changes the pair reserves *before* the gasless swap is priced and settled: [4](#0-3) 

- `amountRepay` (owed to the block proposer / fee-delegation counterparty) is fixed by gas cost math and is verified independently, so the proposer's repayment is protected: [5](#0-4) 
  but the token-to-KAIA `swapForGas` conversion itself is not — any surplus above `amountRepay` becomes `FinalUserAmount`, refunded to the sender from pool liquidity: [6](#0-5) 

### Attack sketch
1. Attacker (an ordinary gasless-tx sender) crafts `GaslessApproveTx` + `GaslessSwapTx(token, amountIn, minAmountOut, amountRepay, deadline)` for a low-liquidity token/KAIA pair registered with `GaslessSwapRouter`.
2. In the same block, before the gasless bundle's position, attacker submits an ordinary swap on the same DEX pair (no special privilege needed) that skews reserves so that `getAmountOut(amountIn)` becomes inflated.
3. `checkBalanceForSwap` admits the tx using this manipulated live rate; at execution, `swapForGas` also resolves against the now-skewed reserves, producing an inflated KAIA output.
4. Fixed `amountRepay` goes to the proposer as usual; the inflated remainder (`FinalUserAmount`) is pocketed by the attacker, extracted from the pool's liquidity providers — a gasless-settlement theft of pool funds, structurally identical to HYDT's spot-price mint exploit.

### Why this matches the required impact bar
This is "gasless ... settlement theft" via unauthorized value movement extracted through oracle/price manipulation, reachable purely by a public, unprivileged gasless transaction sender submitting ordinary transactions within a single block — no privileged role, node compromise, or consensus-message manipulation required.

### Recommendation
`checkBalanceForSwap`/`swapForGas` pricing should not be based purely on spot reserves of an arbitrary registered pair; add TWAP-based or multi-block price checks, minimum-liquidity/deviation bounds relative to a reference price, and/or cap `FinalUserAmount` refund logic so that reserve manipulation within a single block cannot yield a profitable extraction. This is analogous to standard "use TWAP not spot price" guidance from the HYDT/MintV2 class of bugs.

Note: the actual `GaslessSwapRouter` Solidity source is not present in this repository (only ABI bindings), so the precise on-chain enforcement of `minAmountOut`/output computation inside `swapForGas` could not be fully verified from this codebase alone; the Go-side admission check and bundle-ordering behavior described above, however, are confirmed in-repo. [7](#0-6)

### Citations

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L325-330)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterCallerSession) GetAmountIn(token common.Address, amountOut *big.Int) (*big.Int, error) {
	return _GaslessSwapRouter.Contract.GetAmountIn(&_GaslessSwapRouter.CallOpts, token, amountOut)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L332-355)
```go
// GetDEXInfo is a free data retrieval call binding the contract method 0x161efb62.
//
// Solidity: function getDEXInfo(address token) view returns(address factory, address router)
func (_GaslessSwapRouter *GaslessSwapRouterCaller) GetDEXInfo(opts *bind.CallOpts, token common.Address) (struct {
	Factory common.Address
	Router  common.Address
}, error) {
	var out []interface{}
	err := _GaslessSwapRouter.contract.Call(opts, &out, "getDEXInfo", token)

	outstruct := new(struct {
		Factory common.Address
		Router  common.Address
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.Factory = *abi.ConvertType(out[0], new(common.Address)).(*common.Address)
	outstruct.Router = *abi.ConvertType(out[1], new(common.Address)).(*common.Address)

	return *outstruct, err

}
```

**File:** kaiax/gasless/impl/tx_pool.go (L100-142)
```go
}

// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	token := swapArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	g.gaslessInfoMu.RLock()
	swapRouter := g.swapRouter
	g.gaslessInfoMu.RUnlock()

	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

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
	}
```

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
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

**File:** tests/gasless_test.go (L232-243)
```go
	swappedForGasEvent, err := gsrContract.ParseSwappedForGas(*swapTxReceipt.Logs[len(swapTxReceipt.Logs)-1]) // SwappedForGas is issued at the end of swapForGas
	if err != nil {
		t.Fatal(err)
	}
	require.True(t, preState.GetBalance(accounts[0].Addr).Cmp(common.Big0) == 0)
	require.True(t, currentState.GetBalance(accounts[0].Addr).Cmp(swappedForGasEvent.FinalUserAmount) != -1)

	// verify test token balances
	// expected: (current balance) = (pre balance) - swapAmmount
	currentBalanceOfTestAcc, _ := testTokenContract.BalanceOf(&bind.CallOpts{}, accounts[0].Addr)
	require.True(t, currentBalanceOfTestAcc.Cmp(new(big.Int).Sub(preSwapBalanceOfTestAcc, swapAmmount)) == 0)
	t.Logf("final token balance is correct: %v", currentBalanceOfTestAcc.Cmp(new(big.Int).Sub(preSwapBalanceOfTestAcc, swapAmmount)) == 0)
```
