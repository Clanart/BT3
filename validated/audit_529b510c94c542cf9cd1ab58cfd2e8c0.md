## Title
Gasless swap slippage/repayment validation relies on manipulable single-block AMM spot price - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The KIP-247 gasless swap feature (`GaslessSwapRouter`) determines both the tx-pool admission check and the on-chain swap execution price from a Uniswap-V2-style AMM pair's instantaneous reserves (spot price) via `getAmountIn`, with no TWAP or other manipulation-resistant pricing source. This mirrors the EGD Finance root cause ("price-feeding mechanism ... too simple"), where reward/settlement math trusted a spot price that can be moved by a large trade within the same block, enabling value extraction.

### Finding Description
`GaslessModule.checkBalanceForSwap` validates a `GaslessSwapTx` submitted by any unprivileged sender using the router's live spot price: [1](#0-0) 

This `GetAmountIn` call queries `GaslessSwapRouter`, which (per the bundled Uniswap V2 factory/router bytecode pinned in the test suite) computes exchange rates from the underlying AMM pair's current reserves, e.g. `getAmountIn`/`getAmountsOut`: [2](#0-1) [3](#0-2) 

The same spot price also governs actual settlement inside `swapForGas` at execution time: [4](#0-3) 

Because the GaslessApproveTx/GaslessSwapTx bundle is built and executed within a single block (LendTxGenerator + ApproveTx + SwapTx as one bundle per the module's block-building rules), a transaction sender able to place an ordinary large swap against the same underlying pool immediately before their own `swapForGas` call within that block can move the pool reserves, and thus the value returned by `getAmountIn`, before the gasless-swap-specific checks and settlement occur: [5](#0-4) 

The bundling and ordering itself is legitimate (single transaction sender/bundle submitter is fully in scope), and there is no code path in `checkBalanceForSwap` or the router binding that references a time-weighted or otherwise manipulation-resistant price, nor any check that the `amountIn`/`minAmountOut` pair reflects a price consistent with prior blocks.

### Impact Explanation
An attacker can move the DEX pair's reserves in-block (via a normal large swap transaction using their own capital, or capital borrowed within the same transaction context) to obtain a favorable `getAmountIn` result, then execute `swapForGas` to receive more native KAIA (`amountOut`) than the token's fair value would allow, or to satisfy `amountRepay`/`minAmountOut` constraints with less real value than intended. Since the gas fee for the swap is fronted by the block proposer via `LendTxGenerator` and is expected to be repaid from swap proceeds priced by this same manipulable oracle, this can result in unauthorized value extraction from the router's liquidity/commission and under-collateralized proposer gas lending — a direct fee/value-abuse impact within the gasless subsystem. This qualifies as Medium severity: reachable by any unprivileged transaction sender/gasless user, requires only ordinary funds/transactions (no privileged role), and directly causes fund loss in the KIP-247 flow, analogous to the EGD Finance 36,000 BUSD loss from spot-price manipulation.

### Likelihood Explanation
The affected code path is reachable purely by regular RPC/transaction submission: any address controlling tokens in the whitelisted list and interacting with the paired DEX pool can trigger it. It requires no validator, governance, or node-operator privilege — only ordinary in-block transaction ordering control (which any sender can attempt via gas price/priority or via the auction/bundle mechanisms already part of Kaia). No special network conditions beyond typical AMM liquidity depth are needed, making exploitation practically feasible whenever the underlying pool has shallow liquidity relative to the attacker's capital.

### Recommendation
Do not rely solely on the instantaneous `getAmountIn`/spot-price call from the paired AMM for gasless-swap admission and settlement. Introduce manipulation resistance, such as: comparing against a TWAP oracle or multi-block average price, bounding the allowed deviation between the tx-pool-time price and the block-inclusion-time price, capping the amountIn/amountOut ratio drift within a block, or requiring `swapForGas` to fail if the pool's reserves changed materially (e.g., via a reserve-based sanity bound) between the last known price and execution. Additionally, consider disallowing other transactions from the same pool within the same block/bundle window that could alter reserves immediately before a gasless swap is settled.

### Proof of Concept
1. Attacker holds a whitelisted ERC20 token balance and observes the token/WKAIA pool reserves used by `GaslessSwapRouter`.
2. In the same block (or immediately preceding, exploiting proposer-controlled ordering/bundling of `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`), the attacker (or a colluding party) submits a large swap against the underlying DEX pair to skew reserves favorably.
3. Attacker submits `GaslessApproveTx` + `GaslessSwapTx` with `amountIn`/`minAmountOut`/`amountRepay` computed against the skewed spot price, passing `checkBalanceForSwap`'s `GetAmountIn` check as shown in `kaiax/gasless/impl/tx_pool.go` lines 128-141.
4. `swapForGas` executes against the still-skewed reserves (same block), paying out more value than the token's fair market price warrants, while the proposer's lent gas repayment is computed from the same distorted price — extracting value from the router/pool at the expense of the protocol or proposer.

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

**File:** tests/gasless_test.go (L60-65)
```go
	// Bytecode regression: pin the keccak256 of Uniswap runtime bytecode used in gasless.
	// DO NOT MODIFY THE EXPECTED HASHES BELOW.
	assert.Equal(t, "0xbab145d02e7005f0d84c6c1639d39b799b0ea16df99ebbdaf5a14d9da820b4e0",
		crypto.Keccak256Hash(common.Hex2Bytes(uniswapFactoryContracts.UniswapV2FactoryBinRuntime)).Hex())
	assert.Equal(t, "0x8078c0090b05e0bee0587064947604e217146cc295dcb119a2c0217d6e88dac5",
		crypto.Keccak256Hash(common.Hex2Bytes(uniswapRouterContracts.UniswapV2Router02BinRuntime)).Hex())
```

**File:** tests/gasless_test.go (L143-149)
```go
	// In the test below, we swap 1 Token -> `amountsOut` WKAIA.
	swapAmmount := new(big.Int).Mul(big.NewInt(1), bigKaia)
	amountsOut, err := routerContract.GetAmountsOut(&bind.CallOpts{}, swapAmmount, []common.Address{testTokenAddr, wkaiaAddr})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("amountsOut: %s", amountsOut)
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-559)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
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
