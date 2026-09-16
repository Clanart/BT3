## Title
Gasless swap allows a user to avoid paying the protocol commission by setting `minAmountOut == amountRepay` - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The KIP-247 gasless-swap flow only enforces that `minAmountOut >= amountRepay` [1](#0-0) , and that `amountRepay` equals the exact lend/repay amount owed to the proposer [2](#0-1) . There is no requirement that the swap output exceed `amountRepay` by any margin sufficient to cover the router's `commissionRate` fee, which is deducted from the swap output alongside `AmountRepaid` and `FinalUserAmount` [3](#0-2) , [4](#0-3) .

### Finding Description
This mirrors the reported bug class: closing an account exactly at the point where remaining funds only cover principal+interest (but not extra fees) lets the borrower skip paying fees, because the protocol only checks that a loss threshold is respected, not that a positive fee remainder exists. In the Kaia analog, an unprivileged gasless-swap user (any account with zero KAIA balance sending a KIP-247 `GaslessSwapTx`) chooses `token`, `amountIn`, `minAmountOut`, and `amountRepay` themselves. The only pool/state-transition-adjacent invariant enforced before block inclusion is:

- `minAmountOut >= amountRepay` [1](#0-0) 
- `amountIn >= gsr.getAmountIn(minAmountOut)` [5](#0-4) 
- `amountRepay == repayAmount(approveTxOrNil, swapTx)` exactly (SP4) [2](#0-1) 

None of these conditions require the actual swap output (bounded below only by the user-chosen `minAmountOut`) to exceed `amountRepay` by an amount sufficient for the router's `commissionRate`-based fee. Because the user fully controls `minAmountOut` (subject only to `minAmountOut >= amountRepay`), a user can set `minAmountOut = amountRepay` exactly. If the AMM (Uniswap-style `defaultSwapContract`/router) executes the swap and returns an amount close to `minAmountOut` (which the user can also engineer via a thin/self-influenced liquidity path, analogous to the credit-account bug's "arbitrary swap path" issue), the swap output only just covers `amountRepay`, leaving nothing for `commission` or `FinalUserAmount` — the on-chain event even reports these components separately (`AmountRepaid`, `FinalUserAmount`, `Commission`) [3](#0-2) , confirming that commission is carved out of the residual swap output rather than guaranteed independently of user-chosen slippage bounds.

This is structurally identical to the reported vulnerability: the protocol guards against *loss* to the lender (proposer is always repaid `amountRepay` because `minAmountOut >= amountRepay` guarantees the swap won't under-fill the repayment), but does not guard against *fee avoidance* — a user can walk right up to the repayment boundary and pay zero commission, exactly as in the credit-account closure bug where hitting the `loss <= 1` boundary allowed skipping `feeSuccess`/`feeInterest`.

### Impact Explanation
Every gasless-swap transaction can be crafted to bypass the protocol's `commissionRate` fee while still fully repaying the block proposer's lent gas, resulting in systematic fee/revenue leakage for the GaslessSwapRouter operator. This is a concrete, reachable value-extraction path for any unprivileged sender who can submit a `GaslessApproveTx`/`GaslessSwapTx` pair, since gasless senders are exempted from the ordinary balance check (`GetCheckBalance` bypasses standard KAIA-balance validation) [6](#0-5)  and only need to satisfy the SP1–SP4 conditions plus `minAmountOut >= amountRepay`.

### Likelihood Explanation
High likelihood: this requires no privileged role, no validator collusion, and no protocol-level exploit — only choosing `minAmountOut = amountRepay` (or just above it) when constructing a standard `swapForGas` call, a value fully under the caller's control and validated by the tx-pool only as a lower bound, never as a fee-inclusive lower bound [7](#0-6) .

### Recommendation
Enforce that `minAmountOut` (and therefore realized swap output) must exceed `amountRepay` by at least the current `commissionRate`-derived fee, i.e., require `minAmountOut >= amountRepay + commission(amountRepay)` in both the on-chain `swapForGas` implementation and the tx-pool `checkBalanceForSwap` pre-check, analogous to the "Code Corrected" fix in the referenced report which required `remainingFunds > 0` after fee deduction.

### Proof of Concept
1. User acquires a whitelisted ERC-20 token balance and sends `GaslessApproveTx` approving `swapRouter` for `MaxUint256` [8](#0-7) .
2. User computes `amountRepay = repayAmount(approveTx, swapTx)` (the exact lend amount owed) per the module's formula [9](#0-8) .
3. User sets `minAmountOut = amountRepay` (satisfies the only pool check `minAmountOut >= amountRepay`) [1](#0-0)  and sets `amountIn` just sufficient per `GetAmountIn(token, minAmountOut)` [5](#0-4) .
4. Submits `GaslessSwapTx`; the block proposer bundles `[LendTx, GaslessApproveTx, GaslessSwapTx]` [10](#0-9) .
5. On execution, the swap yields output ≈ `minAmountOut = amountRepay`; the proposer is repaid in full via `AmountRepaid`, but `Commission` and `FinalUserAmount` are effectively zero, since the entire output is consumed by repayment — the user pays no commission fee while suffering no penalty, mirroring the reported "avoid paying fees on closure" pattern.

**Uncertainty note:** The actual Solidity source of `GaslessSwapRouter.swapForGas` was not available in the indexed codebase (only compiled bytecode/Go bindings) [11](#0-10) , so the precise arithmetic ordering of commission deduction versus repayment inside the contract could not be directly confirmed from source — this assessment relies on the documented event schema (`AmountRepaid`, `FinalUserAmount`, `Commission`) and the pool-level invariant analysis. A Devin session with full repository/bytecode decompilation access would be needed to confirm the exact on-chain arithmetic.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-120)
```go
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
```

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

**File:** kaiax/gasless/impl/getter.go (L69-86)
```go
// IsApproveTx checks following conditions:
// A1. tx.to is a whitelisted ERC20 token.
// A2. tx.data is `approve(spender, amount)`.
// A3. spender is a whitelisted SwapRouter contract.
// A4. amount is MaxUint.
func (g *GaslessModule) IsApproveTx(tx *types.Transaction) bool {
	args, ok := decodeApproveTx(tx, g.signer)
	return ok && g.isApproveTx(args)
}

func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}
```

**File:** kaiax/gasless/impl/getter.go (L260-263)
```go
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L42-43)
```go
// GaslessSwapRouterBinRuntime is the compiled bytecode used for adding genesis block without deploying code.
const GaslessSwapRouterBinRuntime = `60406080815260048036101561001f575b5050361561001d57600080fd5b005b600091823560e01c8062fa3d50146112ba578063145d51d814611276578063161efb62146111d95780635ea1d6f8146111ba5780635fa7b58414611071578063632db21c14610f12578063715018a614610eac57806375151b6314610e6357806380426901146108915780638da5cb5b1461086b578063c6e85b3b1461039e578063d3c7c2c714610302578063e3bcccb4146102ad578063f2fde38b146101c65763fad99f98146100d05750610010565b346101c257826003193601126101c2576100e86115d5565b479182156101805783808080866001600160a01b038254165af161010a61157a565b501561013e57507f812744101ebaaf6b793a9a3057b00dff294aa41e3665594c617fc101fb0387dc9160209151908152a180f35b6020606492519162461bcd60e51b8352820152601560248201527f436f6d6d697373696f6e436c61696d4661696c656400000000000000000000006044820 ... (truncated)
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L239-254)
```go
// CommissionRate is a free data retrieval call binding the contract method 0x5ea1d6f8.
//
// Solidity: function commissionRate() view returns(uint256)
func (_GaslessSwapRouter *GaslessSwapRouterCaller) CommissionRate(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _GaslessSwapRouter.contract.Call(opts, &out, "commissionRate")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1135)
```go
// GaslessSwapRouterSwappedForGas represents a SwappedForGas event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterSwappedForGas struct {
	Proposer        common.Address
	AmountRepaid    *big.Int
	User            common.Address
	FinalUserAmount *big.Int
	Commission      *big.Int
	Raw             types.Log // Blockchain specific contextual infos
}
```

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
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
