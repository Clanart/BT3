### Title
Front-runnable Uniswap V2 pair initialization allows manipulation of `GaslessSwapRouter` pricing used to authorize gasless swaps - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The `kaiax/gasless` module trusts a UniswapV2-style DEX pool's live reserves as the pricing oracle for gasless swap transactions. `checkBalanceForSwap` calls `GaslessSwapRouter.GetAmountIn(token, minAmountOut)`, which internally reads the pair's `getReserves()` to compute the required `amountIn`. The pair contract is created via `UniswapV2Factory.CreatePair`, and its address is deterministically derivable via CREATE2 before the transaction that creates it and adds initial liquidity is mined - exactly the "vulnerable pool initial rate" class of bug from the referenced report: an attacker can pre-compute the pool address and donate tokens directly to it before/around initialization, skewing the reserve ratio that downstream logic (here, the fee-delegation/gasless price check) relies on.

### Finding Description
`GaslessModule.checkBalanceForSwap` enforces `tx.amountIn >= gsr.getAmountIn(minAmountOut)` by querying the router/pair's on-chain reserves at validation time: [1](#0-0) 

`GaslessSwapRouter.GetAmountIn` (bound in `contracts/bindings/kip247/GaslessSwapRouter.go`) derives the exchange rate from the underlying Uniswap-V2 pool reserves, and the pool itself is created by `UniswapV2Factory.CreatePair`: [2](#0-1) [3](#0-2) 

The end-to-end setup flow shows that `createPair`, deposit/approve, `addLiquidity`, and `AddToken` (which registers the token with `GaslessSwapRouter`) are separate, sequential transactions: [4](#0-3) 

Because `UniswapV2Factory.createPair(tokenA, tokenB)` deploys the pair at a CREATE2 address deterministic from `tokenA`/`tokenB` (standard Uniswap V2 design, exposed via `PairCreated`/`GetPair` in the bindings): [5](#0-4) 

an unprivileged actor monitoring the mempool/public RPC for the `createPair`/`addLiquidity`/`AddToken` sequence can pre-compute the pair address and transfer tokens or WKAIA directly to it before the operator's `AddLiquidity` transaction lands (classic Uniswap-V2 "donation"/first-depositor front-run). Because Uniswap V2's `mint()` computes minted liquidity/implied price from `balanceOf(pair) - reserve`, a donation prior to `addLiquidity` distorts the effective reserve ratio the pool starts with — mirroring the referenced `createPoolADD` vulnerability where an attacker sends tokens to a precomputed pool address before pool creation to bias the initial price.

Since `kaiax/gasless` treats this pool's live reserves as ground truth for authorizing/pricing gasless swap bundles (`checkBalanceForSwap`), a skewed initial rate directly corrupts the fee-delegation/gasless settlement logic: the "required amountIn" computed by the node for admission into the bundle/tx pool would be wrong relative to the pool's true, intended economics, and once `AddToken` registers the token, the node continues to rely on this manipulable pool for every subsequent gasless swap's admission check.

### Impact Explanation
If the initial reserve ratio of the DEX pool backing a gasless-registered token is skewed by a front-run donation, `GetAmountIn`'s pricing is distorted for as long as the imbalance persists (until arbitraged back, which itself can be delayed or exploited by the same attacker). During that window, the gasless swap admission check (`checkBalanceForSwap`) computes an incorrect `requiredAmountIn`, allowing attacker-controlled swap transactions to be admitted into the tx pool/bundle with an unfairly favorable exchange rate. This is fee-delegation/gasless settlement abuse: the attacker can extract value from the `swapRouter`/`depositVault`-style repay mechanism at a rate never intended by the pool's legitimate initial pricing, directly matching the "gasless or auction settlement theft" acceptance criterion.

### Likelihood Explanation
Reachable from a single, unprivileged, publicly submitted transaction: any RPC caller can watch for `UniswapV2Factory.createPair` / `UniswapV2Router02.AddLiquidity` calls (or the associated `GaslessSwapRouter.AddToken` registration) for a newly onboarded gasless token and front-run with a plain token transfer to the deterministic pair address. No special privileges, validator/node access, or governance authority are required — only correctly computing the CREATE2 pair address and beating the operator's liquidity-adding transaction into an earlier block/position, which is a standard, low-cost front-running technique.

### Recommendation
- When any DEX pool is used as a pricing source for `kaiax/gasless` (via `GaslessSwapRouter`/`AddToken`), require that the pool's initial liquidity add happen atomically with pool creation (e.g., a single guarded transaction that creates the pair and adds liquidity, reverting if the pair already holds a non-zero token balance beforehand), consistent with the report's recommended fix of enforcing a zero-balance precondition before initial pricing is fixed.
- In `GaslessModule.checkBalanceForSwap`/`AddToken` flow, add a minimum-liquidity/sanity check (e.g., reject registering or pricing against a pool below a governance-configured liquidity floor, or require a TWAP/multi-block average rather than spot `getReserves()`), so a single-block donation cannot dictate the price used to admit gasless bundles.
- Consider validating that `PairCreated`/`AddToken` only proceed if the pair's `balanceOf` immediately after creation matches the operator's intended deposit, rejecting registration otherwise.

### Proof of Concept
1. Operator plans to launch gasless support for `TestToken`/`WKAIA` by: `UniswapV2Factory.CreatePair(TestToken, WKAIA)` → `WKAIA.Deposit` → approvals → `UniswapV2Router02.AddLiquidity(TestToken, WKAIA, 1000 KAIA, 1000 KAIA, ...)` → `GaslessSwapRouter.AddToken(TestToken, factory, router)`, as shown in the reference setup: [6](#0-5) 
2. An attacker observes the `CreatePair` transaction in the mempool/public RPC and computes the deterministic pair address (CREATE2 based on `TestToken`/`WKAIA`).
3. Attacker submits a transaction transferring a large, skewed amount of `TestToken` (or WKAIA) directly to the pair address before the operator's `AddLiquidity` transaction executes.
4. When `AddLiquidity` executes, Uniswap V2's `mint()` computes reserves/LP shares based on the pair's actual token balances (including the attacker's donation), producing a distorted `reserve0/reserve1` ratio.
5. Once `AddToken` registers the token, `GaslessModule.checkBalanceForSwap` calls `GetAmountIn` (dependent on `getReserves()`) at that skewed ratio: [1](#0-0) , admitting gasless swap transactions priced off the manipulated rate until the market arbitrages the pool back to fair value — during which window the attacker (or colluding searchers) can extract value through the gasless settlement path.

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

**File:** tests/gasless_test.go (L378-474)
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

**File:** contracts/bindings/uniswap/factory/UniswapV2Factory.go (L5788-5807)
```go
// CreatePair is a paid mutator transaction binding the contract method 0xc9c65396.
//
// Solidity: function createPair(address tokenA, address tokenB) returns(address pair)
func (_UniswapV2Factory *UniswapV2FactoryTransactor) CreatePair(opts *bind.TransactOpts, tokenA common.Address, tokenB common.Address) (*types.Transaction, error) {
	return _UniswapV2Factory.contract.Transact(opts, "createPair", tokenA, tokenB)
}

// CreatePair is a paid mutator transaction binding the contract method 0xc9c65396.
//
// Solidity: function createPair(address tokenA, address tokenB) returns(address pair)
func (_UniswapV2Factory *UniswapV2FactorySession) CreatePair(tokenA common.Address, tokenB common.Address) (*types.Transaction, error) {
	return _UniswapV2Factory.Contract.CreatePair(&_UniswapV2Factory.TransactOpts, tokenA, tokenB)
}

// CreatePair is a paid mutator transaction binding the contract method 0xc9c65396.
//
// Solidity: function createPair(address tokenA, address tokenB) returns(address pair)
func (_UniswapV2Factory *UniswapV2FactoryTransactorSession) CreatePair(tokenA common.Address, tokenB common.Address) (*types.Transaction, error) {
	return _UniswapV2Factory.Contract.CreatePair(&_UniswapV2Factory.TransactOpts, tokenA, tokenB)
}
```
