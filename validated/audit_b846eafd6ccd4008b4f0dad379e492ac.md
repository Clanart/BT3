### Title
Gasless swap admission relies on manipulable on-chain AMM spot price, enabling proposer/attacker sandwich to break `minAmountOut` guarantee for gasless users - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The kaia gasless-transaction module admits `SwapTx` bundles into the tx pool and mempool ordering by querying `GaslessSwapRouter.GetAmountIn(token, minAmountOut)`, which in turn derives its price from the underlying whitelisted Uniswap-V2-style DEX pool reserves at call time. Because that spot price is the *same* on-chain reserve state that the transaction itself (and any other transaction in the same block) can move, an unprivileged actor (including the block proposer who controls transaction ordering) can manipulate the pool reserves immediately before a victim's `SwapTx` executes, mirroring the reported "Quoter used as slippage oracle" bug class from the external report.

### Finding Description
`GaslessModule.checkBalanceForSwap` (tx-pool admission / promotion check) validates:
```go
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
...
if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
    return fmt.Errorf("insufficient amountIn: ...")
}
``` [1](#0-0) 

`GaslessSwapRouter.GetAmountIn` is a live on-chain call into the DEX (see `GetAmountIn`/`GetDEXInfo` bindings), which internally reads the current Uniswap-V2 pair reserves — the same reserves manipulable via `swap`, `mint`, or `sync` on the `IUniswapV2Pair` contract in an adjacent transaction of the same block [2](#0-1) . The actual settlement in `swapForGas` also performs the on-chain swap against the same manipulable pool, and the `minAmountOut` value is chosen by the *user* at submission time, based on a spot price observed by the user's client (e.g., via `GetAmountsOut`) [3](#0-2) .

Because the AMM spot price is used both for (a) mempool admission/ordering decisions and (b) actual settlement, a proposer building the block (who has full control over the ordering of transactions it includes, per the kaiax gasless bundling logic in `IsReady`/`isSwapTxReady`) can insert manipulating swap transactions immediately before the victim's gasless `SwapTx` within the same block it assembles [4](#0-3) . This lets the price move against the user between the time the client computed `minAmountOut` and the time the swap actually executes, without the tx-pool admission check catching it (since the admission check itself uses the *manipulated* spot price at inclusion time, not a TWAP or protected price).

### Impact Explanation
If the proposer sandwiches the whitelisted DEX pool right before executing the victim's `SwapTx`, the actual `amountOut` received by the swap can be driven down toward the user-specified `minAmountOut` floor, extracting the difference as sandwich profit. Because `minAmountOut` in this design still must be honored on-chain (the swap reverts if not met), outright fund theft beyond the user's own slippage tolerance is bounded; however, if the user (or an integrating wallet UI) sets a generous `minAmountOut` for reliability, or if `BalanceCheckLevel` is configured below `BalanceCheckLevelSwapAmount`, the manipulation directly degrades `FinalUserAmount` (the KAIA the user receives to pay for future gas) and the commission split, effectively transferring value from the gasless user (and ultimately the fee-delegation/gas-payment guarantee) to the sandwiching proposer. This is a fee-delegation/gasless-settlement value-extraction path reachable purely by transaction submission/ordering, matching the "Medium/High" severity class of manipulable Quoter-derived slippage.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to be, or collude with, the block proposer (who has unilateral control over intra-block transaction ordering for the gasless bundle) and requires a whitelisted token's DEX pool to have thin enough liquidity to move price meaningfully within one block. This is a native capability of any Kaia proposer under round-robin/BFT rotation, not a privileged "malicious validator" exploit outside the reachable transaction-submission threat model — a single block proposer for a single block is sufficient, so it is reachable from an ordinary block-assembly action rather than requiring sustained multi-block network compromise.

### Recommendation
- Do not rely purely on live AMM spot price (`GetAmountIn`/`GetAmountsOut`) for both admission and settlement; incorporate a manipulation-resistant reference price (e.g., TWAP oracle) or bound the allowed price deviation between admission-time quote and execution-time quote.
- Enforce a maximum staleness/deviation between the price observed at tx-pool admission (`checkBalanceForSwap`) and the price used at execution (`swapForGas`), rejecting the swap if reserves shifted beyond a configured threshold within the same block.
- Consider requiring `BalanceCheckLevelSwapAmount` (or stricter) to be mandatory rather than configurable to `BalanceCheckLevelStatic`, since disabling it (as allowed by `GaslessConfig.BalanceCheckLevel`) removes even the spot-price sanity check entirely [5](#0-4) .

### Proof of Concept
1. Whitelist a low-liquidity ERC-20/WKAIA pair for the `GaslessSwapRouter` (as in `setupLiquidity`/`AddToken`) [6](#0-5) .
2. A user submits `ApproveTx` + `SwapTx` (`swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`) with `minAmountOut` computed from the pre-manipulation spot price [7](#0-6) .
3. The block proposer, while assembling the block containing this bundle (`isSwapTxReady`/`IsReady` in tx pool logic), inserts its own large `swap` on the same `IUniswapV2Pair` immediately before the victim's `SwapTx`, moving the reserves so the pool price is worse for the token→WKAIA direction, then reverses it afterward.
4. The user's `SwapTx` still passes admission (`checkBalanceForSwap`) because the price check is done using the reserve state at whatever point the proposer chooses to evaluate/execute it, and the on-chain `swapForGas` succeeds because `minAmountOut` (chosen conservatively by the user) is still met, but `FinalUserAmount` is driven down to near the floor rather than the fair-market amount, and the extracted spread accrues to the sandwiching proposer.

Note: I could not locate the actual Solidity source of `GaslessSwapRouter.swapForGas` (only its compiled bytecode/ABI bindings in `contracts/bindings/kip247/GaslessSwapRouter.go`) within the indexed codebase, so the exact on-chain enforcement details of `minAmountOut`/commission calculation could not be fully verified line-by-line; if the source contains additional protections (e.g., internal TWAP checks) not visible from the bindings, this would reduce or eliminate the described impact. A Devin session with full repository access would be needed to confirm the Solidity source of `GaslessSwapRouter`.

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

**File:** kaiax/gasless/impl/tx_pool.go (L269-290)
```go
// isSwapTxReady assumes that the caller checked `g.IsSwapTx(swapTx)`
func (g *GaslessModule) isSwapTxReady(swapTx, prevTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, swapTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	var approveTx *types.Transaction
	if swapTx.Nonce() == nonce {
		approveTx = nil
	} else if swapTx.Nonce() == nonce+1 {
		if prevTx == nil || !g.IsApproveTx(prevTx) {
			return false
		}
		approveTx = prevTx
	} else {
		return false
	}

	return g.IsExecutable(approveTx, swapTx)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L301-330)
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

// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterCallerSession) GetAmountIn(token common.Address, amountOut *big.Int) (*big.Int, error) {
	return _GaslessSwapRouter.Contract.GetAmountIn(&_GaslessSwapRouter.CallOpts, token, amountOut)
}
```

**File:** tests/gasless_test.go (L142-163)
```go
	/* ------------------------------------ Main test process ------------------------------------- */
	// In the test below, we swap 1 Token -> `amountsOut` WKAIA.
	swapAmmount := new(big.Int).Mul(big.NewInt(1), bigKaia)
	amountsOut, err := routerContract.GetAmountsOut(&bind.CallOpts{}, swapAmmount, []common.Address{testTokenAddr, wkaiaAddr})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("amountsOut: %s", amountsOut)

	var (
		gasPriceBN         = new(big.Int).Mul(big.NewInt(50), bigGkei)
		R1                 = new(big.Int).Mul(big.NewInt(21000), gasPriceBN)
		R2                 = new(big.Int).Mul(big.NewInt(100000), gasPriceBN)
		R3                 = new(big.Int).Mul(big.NewInt(500000), gasPriceBN)
		ammontRepay        = new(big.Int).Add(R1, new(big.Int).Add(R2, R3))
		amountRepaySwap    = new(big.Int).Add(R1, R3)
		transferToken      = new(big.Int).Mul(big.NewInt(100), bigKaia)
		swapExpectedOutput = amountsOut[1]
		margin             = new(big.Int).Div(swapExpectedOutput, big.NewInt(100))
		minAmountOut       = new(big.Int).Add(ammontRepay, margin)
		deadline           = new(big.Int).Add(chain.CurrentBlock().Time(), big.NewInt(300))
	)
```

**File:** tests/gasless_test.go (L446-473)
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
```

**File:** kaiax/gasless/config.go (L64-96)
```go
const (
	BalanceCheckLevelStatic                   = iota // relation between amounts and deadline
	BalanceCheckLevelTokenBalanceAndAllowance        // all above + token balance and allowance
	BalanceCheckLevelSwapAmount                      // all above +	amountIn calculated by dex
	BalanceCheckLevelAll                             // all above +	sender code check
)

type GaslessConfig struct {
	// all tokens are allowed if AllowedTokens is nil while all are disallowed if empty slice
	AllowedTokens         []common.Address `toml:",omitempty"`
	Disable               bool
	MaxBundleTxsInPending uint
	MaxBundleTxsInQueue   uint
	BalanceCheckLevel     int
}

func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}

func (cfg *GaslessConfig) ShouldCheckToken() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelTokenBalanceAndAllowance
}

func (cfg *GaslessConfig) ShouldCheckSwapAmount() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelSwapAmount
}
```

**File:** kaiax/gasless/impl/getter.go (L59-67)
```go
type SwapArgs struct {
	Sender       common.Address // tx.from
	Router       common.Address // tx.to
	Token        common.Address
	AmountIn     *big.Int
	MinAmountOut *big.Int
	AmountRepay  *big.Int
	Deadline     *big.Int
}
```
