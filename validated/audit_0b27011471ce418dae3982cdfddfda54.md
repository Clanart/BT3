### Title
Gasless swap admission and settlement can be manipulated by donating tokens to the underlying AMM pool, bypassing the `GetAmountIn` price-safety gate - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module (KIP-247) lets a proposer lend gas to a fee-less sender, who repays it via a `swapForGas` call on a whitelisted `GaslessSwapRouter`/DEX pool. The only economic sanity check performed before the swap is admitted into a block, `checkBalanceForSwap`, calls the router's `GetAmountIn(token, minAmountOut)`, which is derived directly from the underlying AMM pool's live reserves. Like the Arcadia Aerodrome finding, an attacker who dominates the LP of a whitelisted token/WKAIA pool can donate tokens directly to the pool and call `sync()` (or perform an equivalent reserve-only transfer) to distort the reserve ratio in the same block/nonce sequence right before their `ApproveTx`+`SwapTx` gasless bundle. Because the admission check and the actual on-chain swap execution both read the same manipulated reserves, the attacker can make `getAmountIn` report an artificially tiny required `amountIn` while the swap itself still outputs enough WKAIA to satisfy `minAmountOut` (and thus `amountRepay`), extracting value that isn't backed by the token's real market price.

### Finding Description
`GaslessModule.checkBalanceForSwap` in [1](#0-0)  validates a gasless swap transaction using:
```
requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
...
if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 { ... "insufficient amountIn" }
```
`GetAmountIn` is a live call into the `GaslessSwapRouter` contract [2](#0-1) , which under the hood resolves the token's DEX pool via `GetDEXInfo`/`DexAddress` and derives price from the pool's current reserves (Uniswap-V2-style `getReserves`/`getAmountIn` math, as bound in [3](#0-2)  and the pool's `sync()` entrypoint at [4](#0-3) ).

Critically, the *actual* obligation the sender must satisfy is unrelated to AMM price: `repayAmount`/`lendAmount` are computed purely from the transactions' gas fees, not from the swapped token's fair value [5](#0-4) . The only linkage between the token amount actually put up by the sender and the KAIA value they receive back is the live, unprotected AMM price read at `checkBalanceForSwap`/swap execution time. An attacker who controls most of a whitelisted token/WKAIA pool's liquidity can, in a prior transaction in the same block, transfer extra tokens/WKAIA directly into the pool and call `sync()`, inflating reserves and skewing the exchange rate without losing value (since they can later withdraw proportional liquidity back). Because the mempool's admission gate and the subsequent bundled `swapForGas` execution both observe the same manipulated state, the sender can pass the "insufficient amountIn" check with far less real economic value than the router intended, while still receiving `minAmountOut`/`amountRepay` worth of WKAIA - draining value from the pool's real liquidity providers and/or the proposer's fee-delegation funds, exactly mirroring the "donate + sync to bypass a reserve-derived limit check" root cause of the Arcadia finding.

### Impact Explanation
This allows an unprivileged gasless-transaction sender to bypass the module's intended amountIn/price sanity gate that is supposed to prevent underfunded or economically nonsensical `swapForGas` calls from being admitted and repaid by the proposer's lent gas. Successful exploitation results in value extraction from real pool liquidity (and/or unrecovered lent gas), i.e., concrete fee-delegation/gasless-settlement value theft, rather than a mere denial-of-service or informational issue.

### Likelihood Explanation
The attack requires the attacker to dominate liquidity of a *whitelisted* token's DEX pool (owner-controlled whitelist via `addToken`/`DexAddress`) - this is a meaningful precondition, similar to the original Arcadia bug requiring the attacker to own ~100% of the relevant pool. For low-liquidity or attacker-seeded pools that get whitelisted, this is fully reachable by a single account issuing an ordinary donate+`sync()` transaction immediately followed by their own `ApproveTx`+`SwapTx` gasless bundle within one block, with no special privileges needed.

### Recommendation
Do not rely solely on a single, manipulable spot-reserve read (`GetAmountIn`) for both admission and settlement pricing. Options include: using a TWAP/oracle-resistant price source for the sanity check, bounding the deviation between the reserves observed at admission time versus a longer-window average, capping `amountRepay`/`minAmountOut` relative to a governance-configured safe price band, or requiring the router to validate reserve changes are not attributable to same-block/same-sender donations before trusting `GetAmountIn`.

### Proof of Concept
1. Attacker owns ~100% of the LP for `Token`/`WKAIA` pool that has been added via `GaslessSwapRouter.addToken`/`DexAddress` (see admission path in [6](#0-5) ).
2. In block N, attacker sends a plain transaction that transfers extra `Token` and/or `WKAIA` directly to the pool contract, then calls `sync()` (per [4](#0-3) ), skewing reserves so `Token` appears far more valuable relative to `WKAIA`.
3. In the same block, attacker submits `ApproveTx`+`SwapTx` (`swapForGas`) with a tiny `amountIn` of `Token` and `minAmountOut`/`amountRepay` set to the gas-fee-derived repay amount.
4. `checkBalanceForSwap` ( [7](#0-6) ) calls `GetAmountIn` against the now-skewed reserves and accepts the tiny `amountIn` as sufficient.
5. The proposer's `LendTx` funds the sender's gas per `GetLendTxGenerator`/`lendAmount` ( [8](#0-7) ); the bundled `swapForGas` executes against the same skewed reserves and produces enough WKAIA to satisfy `minAmountOut`/`amountRepay`, even though the real `Token` amount put up was economically worthless.
6. Attacker later withdraws their dominant LP share, recovering most of the donated tokens, having effectively extracted value backed only by the temporary reserve manipulation rather than genuine token value.

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

**File:** contracts/bindings/uniswap/factory/UniswapV2Factory.go (L2665-2691)
```go
// GetReserves is a free data retrieval call binding the contract method 0x0902f1ac.
//
// Solidity: function getReserves() view returns(uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
func (_IUniswapV2Pair *IUniswapV2PairCaller) GetReserves(opts *bind.CallOpts) (struct {
	Reserve0           *big.Int
	Reserve1           *big.Int
	BlockTimestampLast uint32
}, error,
) {
	var out []interface{}
	err := _IUniswapV2Pair.contract.Call(opts, &out, "getReserves")

	outstruct := new(struct {
		Reserve0           *big.Int
		Reserve1           *big.Int
		BlockTimestampLast uint32
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.Reserve0 = *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)
	outstruct.Reserve1 = *abi.ConvertType(out[1], new(*big.Int)).(**big.Int)
	outstruct.BlockTimestampLast = *abi.ConvertType(out[2], new(uint32)).(*uint32)

	return *outstruct, err
}
```

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L2121-2126)
```go
// Sync is a paid mutator transaction binding the contract method 0xfff6cae9.
//
// Solidity: function sync() returns()
func (_IUniswapV2Pair *IUniswapV2PairTransactor) Sync(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _IUniswapV2Pair.contract.Transact(opts, "sync")
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

**File:** kaiax/gasless/impl/getter.go (L268-367)
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

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
}

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
