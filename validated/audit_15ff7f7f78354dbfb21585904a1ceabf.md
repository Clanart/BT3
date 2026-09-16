### Title
Gasless `swapForGas` price check relies on live, unprotected UniswapV2 pool reserves that a public caller can manipulate before the sequenced Approve/Swap bundle executes - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Cork Protocol bug (`M-4`) is a class of "unprotected AMM pricing oracle" issue: a value-critical decision (how much of one asset must be paid for another) is derived from live UniswapV2-style pool reserves at the moment of use, with no minimum-liquidity guarantee and no user-supplied slippage bound tied to the correct reference price. Kaia's gasless-transaction subsystem reproduces the same pattern: `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` validates a public, unprivileged `SwapTx` (`swapForGas`) purely against the *current* on-chain AMM quote obtained from `GaslessSwapRouter.GetAmountIn`, which is itself backed by a UniswapV2 pool [1](#0-0) . There is no check that the underlying pool has adequate/expected liquidity, and the "required" amountIn is computed fresh from whatever reserves exist in the same block, which any address can influence beforehand via ordinary swaps or (for a freshly added token) via being the first liquidity provider.

### Finding Description
`GaslessModule.checkBalanceForSwap` decodes a public `SwapTx` and, when `ShouldCheckSwapAmount()` is enabled, calls the swap router's `GetAmountIn(token, minAmountOut)` to determine the `requiredAmountIn` and only requires `swapArgs.AmountIn >= requiredAmountIn` [2](#0-1) . This getter derives its answer from the live UniswapV2 pair reserves for the token/WKAIA pool that was registered via the router's `AddToken` (owner-only) call, as exercised in the reference integration test that creates the pair and manually seeds liquidity before any check is meaningful [3](#0-2) .

Nowhere in this validation path is there a floor on pool reserves, a check that the pool's price matches an expected/oracle reference rate, or slippage protection tied to a rate the *sender* actually intended. This mirrors Cork's root cause exactly: `__getAmmCtPriceRatio` trusted whatever ratio the AMM pool currently reported, with no floor on liquidity and no fallback to a protocol-defined ratio once the pool had *any* liquidity (even attacker-seeded, skewed liquidity). In Kaia's case, an attacker who is a normal, unprivileged transaction sender can:

1. Wait for (or induce, if reachable) a state where the gasless pool for a given `allowedToken` has thin liquidity (e.g., right after `AddToken`, or after draining most liquidity via ordinary swaps a public caller can always submit).
2. Perform a swap against that pool (a permissionless AMM action any address can call) to skew the token:WKAIA ratio in the pool.
3. Immediately submit their own `SwapTx` (`swapForGas`) with `amountIn`/`minAmountOut` computed against this skewed price. `GetAmountIn` will report an artificially low `requiredAmountIn` for the actual `minAmountOut` the attacker wants (or, conversely, an artificially generous `minAmountOut` for a fixed `amountIn`), since it is only checking internal consistency of the manipulated pool state at that instant, not consistency with any external/expected exchange rate.
4. `checkBalanceForSwap` accepts the tx because `swapArgs.AmountIn >= requiredAmountIn` holds relative to the manipulated pool, letting the bundling/sequencing logic (`isSwapTxReady`/`IsExecutable` in `kaiax/gasless/impl/getter.go`) admit the transaction into the gasless bundle flow and get gas-fee lending executed via `MakeLendTx`.
5. The gasless swap itself then executes against the same skewed pool, extracting value (paying too little token for the WKAIA/gas that gets repaid to the lending validator, or draining WKAIA reserves that back other gasless users), exactly analogous to the LV/AMM drain scenario in the Cork POC where the protocol added liquidity/settled at an attacker-set ratio and then the attacker arbitraged.

Because `checkBalanceForSwap` is purely a mempool/tx-pool admission check (not consensus-enforced pricing done by the contract itself with slippage protection independent from spot reserves), there is no defense-in-depth: whatever the pool momentarily reports is treated as ground truth for whether to admit and later execute the fee-delegated bundle.

### Impact Explanation
This allows an unprivileged, unauthenticated public RPC caller (any gasless-swap sender) to manipulate the price used to gate and settle a fee-delegation/lending flow that moves real value (WKAIA repayment to the block proposer/lender and token flow through the router). Similar to the Cork finding being escalated to Medium because of arbitrage-driven value extraction and devaluation of pooled liquidity, this pattern in Kaia's gasless module can result in: incorrect acceptance of underpriced `SwapTx`s, drainage of the router's paired liquidity, and financial loss to the gas-lending mechanism or other gasless users relying on the same pool, all without requiring any privileged role or off-chain trust assumption.

### Likelihood Explanation
Medium. The attack requires the ability to swap against the specific token/WKAIA pool used by `GaslessSwapRouter` and to then submit a `SwapTx` in the same block/sequence — both are permissionless actions available to any transaction sender. It is most acute immediately after a new token is registered via `AddToken` (parallel to Cork's freshly-issued, empty AMM pair window) or whenever pool reserves are thin, and is less severe once a pool has deep, continuously-arbitraged liquidity — mirroring the debate in the Cork discussion about whether such windows are "real" attack surface or a systemic/liquidity-risk issue that the operator (here, whichever entity calls `AddToken`) is responsible for mitigating.

### Recommendation
- Do not derive the pass/fail admission decision in `checkBalanceForSwap` purely from the router's live `GetAmountIn` quote; also compare against a minimum-liquidity threshold for the pool and/or an external reference price before accepting a `SwapTx`.
- Consider requiring `GaslessSwapRouter`/`AddToken` to atomically bootstrap and lock a minimum amount of liquidity (owner-provided) for any newly whitelisted token before it becomes eligible for gasless swaps, closing the "empty/thin pool" window analogous to Cork's recommendation to gate AMM initialization through a trusted config/owner path.
- Add explicit slippage/rate-of-change bounds so that swap admission is robust to same-block or recent-block price manipulation of the underlying pool, rather than trusting instantaneous reserves.

### Proof of Concept
Conceptual reproduction (mirroring the Cork POC structure), based on the code paths inspected:
1. Owner registers a new ERC20 token in `GaslessSwapRouter` via `AddToken`, creating (or referencing) its UniswapV2 pair with WKAIA, as done in the test helper `setupLiquidity`/`AddToken` flow [4](#0-3) .
2. Before/while the pool has thin liquidity, an attacker (any address) submits ordinary UniswapV2 swaps against the pair to skew the token:WKAIA ratio.
3. Attacker crafts a `swapForGas` `SwapTx` with `amountIn`/`minAmountOut` chosen to pass `checkBalanceForSwap`'s check `swapArgs.AmountIn.Cmp(requiredAmountIn) >= 0`, where `requiredAmountIn` is computed from the now-skewed pool via `routerContract.GetAmountIn(nil, token, minAmountOut)` [2](#0-1) .
4. Because no minimum-liquidity or reference-price check exists, the tx is admitted into the gasless bundle pipeline (`isSwapTxReady`/`IsExecutable`) and the fee-lending logic executes the swap at the attacker-favorable rate, extracting value from the pool/lending mechanism.

Note: The exact Solidity source of `GaslessSwapRouter.GetAmountIn`/`AddToken` was not available in the indexed codebase (only compiled Go bindings were found under `contracts/bindings/kip247/GaslessSwapRouter.go`), so the precise on-chain reserve-check/slippage logic inside the contract could not be fully verified. A Devin session with full repository access could confirm whether `GetAmountIn` includes any internal minimum-liquidity guard that would mitigate this analog.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L128-142)
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
	}
```

**File:** tests/gasless_test.go (L378-460)
```go
func setupLiquidity(t *testing.T, owner *TestAccountType, contracts contractsForGasless, chain *blockchain.BlockChain) {
	var (
		testTokenAddr     = contracts.testTokenAddr
		testTokenContract = contracts.testTokenContract
		wkaiaAddr         = contracts.wkaiaAddr
		wkaiaContract     = contracts.wkaiaContract
		factoryAddr       = contracts.factoryAddr
		factoryContract   = contracts.factoryContract
		routerAddr        = contracts.routerAddr
		routerContract    = contracts.routerContract
		gsrContract       = contracts.gsrContract
		initialLiquidity  = new(big.Int).Mul(big.NewInt(1000), bigKaia)
	)

	/* ------------- create pair ------------- */
	createPairTx, err := factoryContract.CreatePair(bind.NewKeyedTransactor(owner.Keys[0]), testTokenAddr, wkaiaAddr)
	if err != nil {
		t.Fatal(err)
	}
	createPairReceipt := waitReceipt(chain, createPairTx.Hash())
	if createPairReceipt == nil || createPairReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to create pair")
	}
	owner.Nonce += 1

	/* ------------- deposit ------------- */
	optsForDeposit := bind.NewKeyedTransactor(owner.Keys[0])
	optsForDeposit.Value = initialLiquidity
	optsForDeposit.GasLimit = 300000
	depositTx, err := wkaiaContract.Deposit(optsForDeposit)
	if err != nil {
		t.Fatal(err)
	}
	depositReceipt := waitReceipt(chain, depositTx.Hash())
	if depositReceipt == nil || depositReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to deposit")
	}
	owner.Nonce += 1

	/* ------------- approve(TestToken) ------------- */
	testTokenApproveTx, err := testTokenContract.Approve(bind.NewKeyedTransactor(owner.Keys[0]), routerAddr, initialLiquidity)
	if err != nil {
		t.Fatal(err)
	}
	testTokenApproveReceipt := waitReceipt(chain, testTokenApproveTx.Hash())
	if testTokenApproveReceipt == nil || testTokenApproveReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to approve(TestToken)")
	}
	owner.Nonce += 1

	/* ------------- approve(WKAIA) ------------- */
	wkaiaApproveTx, err := wkaiaContract.Approve(bind.NewKeyedTransactor(owner.Keys[0]), routerAddr, initialLiquidity)
	if err != nil {
		t.Fatal(err)
	}
	wkaiaApproveReceipt := waitReceipt(chain, wkaiaApproveTx.Hash())
	if wkaiaApproveReceipt == nil || wkaiaApproveReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to approve(WKAIA)")
	}
	owner.Nonce += 1

	balanceOfWKAIA, _ := wkaiaContract.BalanceOf(&bind.CallOpts{}, owner.Addr)
	balanceOfTestToken, _ := testTokenContract.BalanceOf(&bind.CallOpts{}, owner.Addr)
	wallowance, _ := wkaiaContract.Allowance(&bind.CallOpts{}, owner.Addr, routerAddr)
	tallowance, _ := testTokenContract.Allowance(&bind.CallOpts{}, owner.Addr, routerAddr)
	t.Log("balance of tokens: ", balanceOfWKAIA, balanceOfTestToken)
	t.Log("allowances of tokens: ", wallowance, tallowance)

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
```

**File:** tests/gasless_test.go (L462-474)
```go
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
