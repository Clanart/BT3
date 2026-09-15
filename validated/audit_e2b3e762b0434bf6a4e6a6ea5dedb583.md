### Title
Gasless module skips native-value balance validation for swap/approve transactions - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The reported `borgCore.checkTransaction()` bug is a class of vulnerability where a transaction-authorization path checks calldata/permissions but fails to validate or restrict the native call value attached to the transaction. The Kaia `kaiax/gasless` module has an analogous gap: when a transaction is recognized as a gasless "approve" or "swap" module transaction, the transaction pool's standard sender-balance/cost check (which covers `tx.Value()` plus gas) is entirely bypassed in favor of the module's own `GetCheckBalance()` logic, but that logic never validates the transaction's native `Value()` field at all.

### Finding Description
In `blockchain/tx_pool.go`, `validateTx` performs the normal balance check `senderBalance.Cmp(tx.Cost()) < 0` (which is `V + GP*GL`) only when no pool module claims the transaction: [1](#0-0) [2](#0-1) 

When a module (here, the gasless module) recognizes the tx via `IsModuleTx`, `shouldSkipBalanceCheck` is set to `true` and the module's own `checkBalance` function is used instead, and the default `senderBalance.Cmp(tx.Cost())` check is skipped entirely.

The gasless module marks a transaction as a module tx purely based on calldata pattern-matching (`IsApproveTx`/`IsSwapTx`), independent of the transaction's `Value()` field: [3](#0-2) [4](#0-3) 

The module-specific balance checks (`checkBalanceForApprove`, `checkBalanceForSwap`) only validate ERC20 token balances/allowances, swap amounts, and deadlines — they never check that the sender actually has sufficient native KAIA balance to cover `tx.Value()` (plus gas): [5](#0-4) 

This mirrors the reported bug class: an authorization/validation path (`checkTransaction`/`validateTx`) that inspects calldata but has no corresponding restriction on the native value field, allowing a value-bearing message to slip past the checks that would normally apply to plain value transfers.

### Impact Explanation
An attacker-controlled EOA can craft a gasless `approve`/`swapForGas` transaction (matching the whitelisted token/router selector conditions) with an arbitrary nonzero `Value()` that the sender does not actually have covered against the standard `tx.Cost()` rule, since that rule is bypassed for module transactions. While execution-time balance checks in the EVM state transition would still reject an insufficiently funded transfer, the transaction-pool-level admission logic diverges from the invariant enforced for all other transaction types (that sufficient balance for value+fees is validated on pool admission, not deferred to block execution). This creates room for transaction-pool spam/admission-inconsistency between the gasless flow and normal flow, and any future logic that trusts `IsModuleTx`-checked-balance sufficiency (e.g., the "lending" flow in `GetLendTxGenerator`, which sends the proposer's native KAIA to the swap sender based on assumptions about swap tx validity) could be built on the false assumption that admitted swap/approve txs have their value fully backed by balance.

### Likelihood Explanation
Reachable by any unprivileged transaction sender who can submit a raw transaction to a public RPC node that matches the gasless module's `approve`/`swapForGas` selector and whitelisted token/router addresses — no special privilege required. The only precondition is that the token and router are on the gasless allowlist, which is a normal operating configuration of the module, not an attacker-controlled parameter.

### Recommendation
Add an explicit native-value check within `checkBalanceForApprove`/`checkBalanceForSwap` (or in `GetCheckBalance`) ensuring `senderBalance >= tx.Value() + feePayerCost` consistent with the default `tx.Cost()` check that non-module transactions receive in `blockchain/tx_pool.go`'s `validateTx`. Do not let `shouldSkipBalanceCheck` fully bypass native-value/fee balance validation for module transactions; instead compose the module-specific token checks with the same value/fee coverage check applied to ordinary transactions.

### Proof of Concept
1. Configure gasless module with allowed token `T` and router `R`.
2. Attacker constructs a legacy transaction `tx` calling `T.approve(R, MaxUint256)` (or `R.swapForGas(...)` with valid amounts/deadline) but sets `tx.Value()` to a large nonzero amount that the sender's account balance cannot cover.
3. Submit `tx` via `eth_sendRawTransaction`.
4. In `blockchain/tx_pool.go` `validateTx`, `module.IsModuleTx(tx)` returns true (`kaiax/gasless/impl/tx_pool.go:55-60`), so `shouldSkipBalanceCheck = true` and only `GetCheckBalance()` (`kaiax/gasless/impl/tx_pool.go:62-72`) is run, which never inspects `tx.Value()`.
5. The transaction is admitted to the pool despite the sender lacking funds to cover the declared native value, unlike every other transaction type where `senderBalance.Cmp(tx.Cost())` would reject it (`blockchain/tx_pool.go:983-988`).

### Citations

**File:** blockchain/tx_pool.go (L918-932)
```go
	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
	}
```

**File:** blockchain/tx_pool.go (L983-988)
```go
	} else if !shouldSkipBalanceCheck {
		// balance check for non-fee-delegated tx
		if senderBalance.Cmp(tx.Cost()) < 0 {
			logger.Trace("[tx_pool] insufficient funds for cost(gas * price + value)", "from", from, "balance", senderBalance, "cost", tx.Cost())
			return ErrInsufficientFundsFrom
		}
```

**File:** kaiax/gasless/impl/tx_pool.go (L55-72)
```go
func (g *GaslessModule) IsModuleTx(tx *types.Transaction) bool {
	if tx == nil {
		return false
	}
	return g.IsApproveTx(tx) || g.IsSwapTx(tx)
}

func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L74-182)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
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

	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
	}

	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/getter.go (L69-103)
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
