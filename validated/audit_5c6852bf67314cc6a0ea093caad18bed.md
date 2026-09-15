### Title
Stale AMM spot-price check in Gasless module's tx-pool admission enables settlement under-collection - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The Rikkei Finance exploit's root cause was a security-critical financial decision (borrow authorization) being driven by a price value that an unprivileged actor could set/manipulate immediately before use, with no re-validation at the point of value settlement. The Kaia `kaiax/gasless` module has an analogous pattern: the tx-pool admission check for a `swapForGas` transaction reads a live AMM spot price via `GaslessSwapRouter.GetAmountIn` and uses it as the sole gate for accepting the declared `amountIn`/`amountRepay` values, but this check is only re-evaluated on admission/promotion, not re-verified atomically at the moment of on-chain settlement inside the same block.

### Finding Description
`GaslessModule.checkBalanceForSwap` validates a submitted `swapForGas` transaction by querying the router's `getAmountIn(token, minAmountOut)` and requiring `tx.amountIn >= requiredAmountIn`: [1](#0-0) 

`GetAmountIn` is a live, on-chain call into `GaslessSwapRouter`, which is documented and tested to source its exchange rate from a Uniswap V2-style pool's current reserves (spot price): [2](#0-1) [3](#0-2) 

Because this is a spot-price read from mutable pool reserves, any unprivileged sender can shift the price in the same block by executing an ordinary swap on the underlying DEX pair before the gasless `swapForGas` transaction is processed. The tx-pool validation (`GetCheckBalance`/`checkBalanceForSwap`) is invoked during pool admission/promotion — a point in time distinct from the moment the `swapForGas` call is actually executed during block assembly/state transition. There is no evidence in the accessible code paths that the same reserve-based check is re-verified transactionally at the exact execution point guarded by consensus (unlike, e.g., KIP‑71's `VerifyMagmaHeader`, which cryptographically pins the base fee to the previous block's state so it cannot be manipulated intra-block): [4](#0-3) 

This is structurally the same bug class as Rikkei: a value-critical check (there: borrow limit; here: whether declared `amountIn` sufficiently backs the promised `amountRepay`/`minAmountOut`) is derived from a source (`SimplePriceOracle` there; the DEX pool's spot reserves here) that an ordinary, unprivileged transaction sender can move between the time the check is performed and the time the guarded action is settled.

### Impact Explanation
If the actual reserve state at execution time diverges from the reserve state used during tx-pool admission (via an attacker's own prior swap in the same block, or normal price movement), a `swapForGas` transaction that was admitted as "sufficiently funded" could execute against a worse price, resulting in the router either reverting (denial of gasless service) or settling with fewer output tokens than `amountRepay` required to reimburse the fee-delegation counterparty (block proposer/relayer), i.e., a form of fee/gasless settlement short-fall. Because the check is advisory (performed by the node's tx-pool logic, not enforced deterministically by the protocol at the execution boundary), different nodes evaluating the check at different local states could also reach different admission decisions for the same transaction, risking state/consensus divergence in gasless transaction promotion.

### Likelihood Explanation
Exploitability requires only an unprivileged actor able to submit an ordinary DEX swap transaction ahead of the gasless `swapForGas` transaction within the same block (or across the tx-pool-check-to-inclusion window), which is fully within reach of any public-RPC/tx-pool caller — no special privilege, validator, or node compromise is required. The severity is bounded by the size of the underlying DEX pool's liquidity and by whatever slippage protections exist inside the (closed-source, only bytecode/ABI available) `GaslessSwapRouter.swapForGas` implementation itself, which could not be fully verified from the accessible Solidity/Go sources in this repository.

### Recommendation
Re-validate `amountIn`/`amountRepay` sufficiency against the exact reserve state at the point of `swapForGas` execution (not just at tx-pool admission), and/or require the on-chain `GaslessSwapRouter.swapForGas` call itself to enforce `minAmountOut`/`amountRepay` atomically against actual swap output with a hard revert-on-shortfall guarantee, so that no economic settlement can occur unless the real-time swap output covers the promised repayment. Consider tightening `IsReady`/promotion logic so gasless swap transactions are re-checked immediately before inclusion, minimizing the admission-to-execution window in which the AMM price can be moved by an unprivileged actor.

### Proof of Concept
Full verification (including the un-reviewable closed-source `swapForGas` settlement enforcement) requires runtime confirmation. Conceptually mirroring the referenced Rikkei PoC pattern:
1. Attacker observes a pending `swapForGas` transaction with declared `amountIn`, `minAmountOut`, `amountRepay` that just satisfies `checkBalanceForSwap`'s `GetAmountIn` check against the current pool reserves.
2. Attacker submits (or bundles ahead) an ordinary swap on the same underlying DEX pair used by `GaslessSwapRouter.DexAddress(token)`, shifting reserves and worsening the effective price for the pending gasless swap.
3. When the gasless swap executes later in the same block, the real swap output is less than what `amountRepay`/`minAmountOut` required, but the transaction was already admitted under the earlier, now-stale price.
This PoC could not be fully executed/confirmed against actual bytecode behavior of `swapForGas` in this session; the `checkBalanceForSwap`/`GetAmountIn` TOCTOU exposure itself is confirmed directly from source at: [5](#0-4)

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
```go
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L301-316)
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
```

**File:** tests/gasless_test.go (L100-149)
```go
	/* ------------- Deploy contracts ------------- */
	testTokenAddr, testTokenContract := deployTestToken(t, chain, transactor, owner, owner.Addr)
	wkaiaAddr, wkaiaContract := deployWKAIA(t, chain, transactor, owner)
	factoryAddr, factoryContract := deployUniswapV2Factory(t, chain, transactor, owner, owner.Addr)
	routerAddr, routerContract := deployUniswapV2Router02(t, chain, transactor, owner, factoryAddr, wkaiaAddr)
	gsrAddr, gsrContract := deployGaslessSwapRouter(t, chain, transactor, owner, wkaiaAddr)

	/* ------------- Register GaslessSwapRouter address in Registry ------------- */
	// send register tx
	targetBlockNum := new(big.Int).Add(node.BlockChain().CurrentHeader().Number, big.NewInt(4))
	registry, err := kip149contract.NewRegistry(system.RegistryAddr, transactor)
	if err != nil {
		t.Fatal(err)
	}
	registerTx, err := registry.Register(bind.NewKeyedTransactor(owner.Keys[0]), gaslessImpl.GaslessSwapRouterName, gsrAddr, targetBlockNum)
	if err != nil {
		t.Fatal(err)
	}
	registerTxReceipt := waitReceipt(chain, registerTx.Hash())
	if registerTxReceipt == nil || registerTxReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to registor GaslessSwapRouter address")
	}

	// wait target block
	targetHeader := waitBlock(chain, targetBlockNum.Uint64())
	require.NotNil(t, targetHeader)

	/* ------------- Set up initial liquidity ------------- */
	contracts := contractsForGasless{
		testTokenAddr:     testTokenAddr,
		testTokenContract: testTokenContract,
		wkaiaAddr:         wkaiaAddr,
		wkaiaContract:     wkaiaContract,
		factoryAddr:       factoryAddr,
		factoryContract:   factoryContract,
		routerAddr:        routerAddr,
		routerContract:    routerContract,
		gsrAddr:           gsrAddr,
		gsrContract:       gsrContract,
	}
	setupLiquidity(t, owner, contracts, chain)

	/* ------------------------------------ Main test process ------------------------------------- */
	// In the test below, we swap 1 Token -> `amountsOut` WKAIA.
	swapAmmount := new(big.Int).Mul(big.NewInt(1), bigKaia)
	amountsOut, err := routerContract.GetAmountsOut(&bind.CallOpts{}, swapAmmount, []common.Address{testTokenAddr, wkaiaAddr})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("amountsOut: %s", amountsOut)
```

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
}
```
