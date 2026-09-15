### Title
Gasless-swap admission relies on instantaneous UniswapV2 pool reserves as sole price source, enabling sandwich manipulation of `swapForGas` pricing - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
Kaia's gasless-transaction module validates a user's `SwapTx` (`swapForGas`) by calling `GaslessSwapRouter.GetAmountIn(token, minAmountOut)`, which — like the Uniswap-V2-style router bound in `contracts/bindings/kip247/GaslessSwapRouter.go` and `contracts/bindings/uniswap/router/UniswapV2Router02.go` — derives the exchange rate directly from the live reserves of a single on-chain liquidity pool (`getReserves`/`quote`-style constant-product math), with no TWAP, external oracle, or multi-source price check. This is the same bug class as the Definer/OEC incident: a DeFi price computation trusts one liquidity pool's spot balance at a point in time as ground truth, which any unprivileged trader can manipulate atomically via a large swap in the same block.

### Finding Description
`GaslessModule.checkBalanceForSwap` (mempool admission check for gasless swap transactions) computes the required `amountIn` like this: [1](#0-0) 

`routerContract.GetAmountIn(nil, token, minAmountOut)` calls into `GaslessSwapRouter`, which resolves the DEX pair and router for the token via `GetDEXInfo`/`DexAddress`: [2](#0-1) [3](#0-2) 

and ultimately relies on the UniswapV2Router `quote`/`getAmountsIn` math, whose price is a pure function of the pair's current reserves (`reserveA`, `reserveB`) — i.e., a single liquidity pool's token balances at the moment of the call: [4](#0-3) 

This is architecturally identical to the Definer incident: "the token balance of a single liquidity pool at a point in time" is used as the price source, with no protection against an attacker who moves the pool's reserves immediately before the priced call executes (flash swap / large same-block trade), then reverts the position afterward, extracting value from anyone whose price is computed off that transiently-skewed pool.

In Kaia's gasless flow, the pool used for pricing is deployed and controlled once via `AddToken(token, factory, router)` (see `tests/gasless_test.go` `setupLiquidity`), and is a normal permissionless AMM pool — any address can add liquidity to or trade against it: [5](#0-4) 

Because `checkBalanceForSwap` is invoked both at mempool admission and is the sole automated on-chain-state check gating whether a `SwapTx` is treated as a valid gasless transaction (feeding into `IsExecutable`/bundle building with a fee-delegated `LendTx`), an attacker who sandwiches the target pool around the block containing the `ApproveTx`+`SwapTx` bundle can distort `GetAmountIn`'s output, either:
- forcing `requiredAmountIn` artificially low so an undercollateralized swap passes the `AmountIn >= requiredAmountIn` check while the pool has been manipulated to make the trade unprofitable/valueless at execution, or
- causing repeated legitimate `SwapTx`s to be rejected/exploited around the manipulation window, undermining the gasless module's promised guarantee that `AmountIn` covers the value repaid to the fee-delegating validator (`RepayAmount`).

The `VerifyExecutable`/repay accounting (`repayAmount`, `lendAmount`) assumes the swap's economic value (as validated via the manipulable oracle) is trustworthy at both admission time and execution time: [6](#0-5) [7](#0-6) 

### Impact Explanation
The gasless module fee-delegates gas via a `LendTx` funded by the block proposer/validator, expecting to be repaid from the swap proceeds computed against the pool. If the price check that gates swap admission (`GetAmountIn`) can be manipulated by an unprivileged trader manipulating the underlying DEX pool's reserves in the same or adjacent block, an attacker can cause the gasless accounting to accept swaps priced off a distorted pool state, directly risking fee/repayment abuse for the fee-delegation counterparty (the block proposer that fronts gas) and/or theft of value from the gasless settlement path — matching the "gasless...settlement theft" and "fee-delegation abuse" categories explicitly in scope. This is a systemic pricing flaw reachable by any public swap participant, not merely a griefing/DoS issue.

### Likelihood Explanation
Likelihood is Medium: the attack requires the attacker to control (or be able to trade against) the specific DEX pool registered for a given gasless token, and to time a manipulation transaction/bundle around a pending gasless `SwapTx`. Any unprivileged trader can submit ordinary swap transactions against the same public AMM pool used by `GaslessSwapRouter`, and Kaia's public mempool/RPC exposes pending gasless transactions for front-running/sandwiching, making this practically reachable without special privilege — the same practical difficulty that made the original Definer/OEC oracle attack (30,765 CHE) realizable.

### Recommendation
Do not price gasless-swap collateral requirements solely from a single AMM pool's instantaneous reserves. Use a manipulation-resistant price source such as a time-weighted average price (TWAP) across a window of blocks, cross-check against multiple independent pools/oracles, or apply a conservative slippage/deviation bound and re-validate the price at both `TxPool` admission and just before block inclusion, rejecting swaps if the quoted price has moved beyond a safe threshold within the recent block window.

### Proof of Concept
1. Attacker identifies a whitelisted gasless token whose registered DEX (via `GaslessSwapRouter.GetDEXInfo`) is a standard UniswapV2-style pool with limited liquidity (as set up analogously in `tests/gasless_test.go` `setupLiquidity`).
2. Attacker submits a large swap transaction against that pool immediately before/alongside a victim's `ApproveTx`+`SwapTx` gasless bundle is picked up, shifting the pool's reserve ratio.
3. `GaslessModule.checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:107-181`) calls `GetAmountIn` against the now-skewed pool, producing a `requiredAmountIn` that no longer reflects the pool's normal/fair price.
4. The victim's (or an attacker-crafted) `SwapTx` is admitted/executed using this distorted valuation, and the attacker reverses their manipulating trade in the same block (flash-swap style), extracting the price difference while the gasless repayment accounting (`repayAmount`/`lendAmount`) proceeds on the manipulated basis — reproducing the Definer-style single-pool-oracle exploit within Kaia's gasless settlement path.

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L301-323)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterCaller) GetAmountIn(opts *bind.CallOpts, token common.Address, amountOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _GaslessSwapRouter.contract.Call(opts, &out, "getAmountIn", token, amountOut)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterSession) GetAmountIn(token common.Address, amountOut *big.Int) (*big.Int, error) {
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

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L4069-4089)
```go
// Quote is a free data retrieval call binding the contract method 0xad615dec.
//
// Solidity: function quote(uint256 amountA, uint256 reserveA, uint256 reserveB) pure returns(uint256 amountB)
func (_IUniswapV2Router02 *IUniswapV2Router02Caller) Quote(opts *bind.CallOpts, amountA *big.Int, reserveA *big.Int, reserveB *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _IUniswapV2Router02.contract.Call(opts, &out, "quote", amountA, reserveA, reserveB)
	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err
}

// Quote is a free data retrieval call binding the contract method 0xad615dec.
//
// Solidity: function quote(uint256 amountA, uint256 reserveA, uint256 reserveB) pure returns(uint256 amountB)
func (_IUniswapV2Router02 *IUniswapV2Router02Session) Quote(amountA *big.Int, reserveA *big.Int, reserveB *big.Int) (*big.Int, error) {
	return _IUniswapV2Router02.Contract.Quote(&_IUniswapV2Router02.CallOpts, amountA, reserveA, reserveB)
}
```

**File:** tests/gasless_test.go (L446-474)
```go
	/* ------------- add liquidity ------------- */
	optsForAddLiquidity := bind.NewKeyedTransactor(owner.Keys[0])
	optsForAddLiquidity.GasLimit = 3000000
	deadline := time.Now().Unix() + 60*20
	addLiquidityTx, err := routerContract.AddLiquidity(optsForAddLiquidity, testTokenAddr, wkaiaAddr,
		initialLiquidity, initialLiquidity, common.Big0, common.Big0, owner.Addr, big.NewInt(deadline))
	if err != nil {
		t.Fatal(err)
	}
	addLiquidityReceipt := waitReceipt(chain, addLiquidityTx.Hash())
	if addLiquidityReceipt == nil || addLiquidityReceipt.Status != types.ReceiptStatusSuccessful {
		t.Log(addLiquidityReceipt)
		t.Fatal("failed to add liquidity")
	}
	owner.Nonce += 1

	/* ------------- add token to gsr ------------- */
	optsForAddToken := bind.NewKeyedTransactor(owner.Keys[0])
	optsForAddToken.GasLimit = 300000
	addTokenTx, err := gsrContract.AddToken(optsForAddToken, testTokenAddr, factoryAddr, routerAddr)
	if err != nil {
		t.Fatal(err)
	}
	addTokenReceipt := waitReceipt(chain, addTokenTx.Hash())
	if addTokenReceipt == nil || addTokenReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to add token to gsr")
	}
	owner.Nonce += 1
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

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```
