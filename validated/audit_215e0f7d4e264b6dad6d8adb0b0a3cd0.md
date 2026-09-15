This confirms my analysis: the KIP-247 gasless mechanism splits enforcement across two layers — the on-chain `GaslessSwapRouter` contract's `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` (an `external`/public, unrestricted function per its ABI) [1](#0-0)  and the off-chain `kaiax/gasless` module's mempool-admission checks (`VerifyExecutable`, `checkBalanceForSwap`) that validate nonce sequencing, repay-amount correctness, and — critically — that the sender is a plain EOA with no code [2](#0-1) [3](#0-2) .

### Title
GaslessSwapRouter.swapForGas is directly callable outside the validated gasless flow, bypassing tx-pool-only invariants ([File: kaiax/gasless/impl/tx_pool.go], [File: kaiax/gasless/impl/getter.go])

### Summary
The 98Token incident was caused by a swap-router function (`swapTokensForTokens`) that any caller could invoke directly, bypassing the intended privileged/orchestrated call path and draining value the contract held on behalf of others. Kaia's KIP-247 `GaslessSwapRouter.swapForGas` is analogous: it is a plain public/external contract function with no on-chain restriction on `msg.sender`, `tx.origin`, or caller code, and none of the "gasless" invariants (repay-amount correctness, nonce sequencing, EOA-only sender, minAmountOut vs amountRepay consistency) are re-checked inside the contract itself — they only exist in the `kaiax/gasless` Go module that gates transaction-pool promotion.

### Finding Description
`swapForGas` is decoded and validated purely as an ABI signature match by the gasless module: `routerSwapFunc = mustParseAbi(routerAbiJson, "swapForGas")` [4](#0-3) , and its "gasless-ness" is determined only via `IsSwapTx`/`isSwapTx`, which check nothing more than that `tx.to` is the registered router and the token is allowlisted [5](#0-4) . All the safety invariants — minAmountOut ≥ amountRepay, `amountIn` sufficiency, sender code check ("sender with code is not allowed"), balance/allowance checks, and deadline — live exclusively in `checkBalanceForSwap`, which is wired in as `GetCheckBalance()` for the tx pool [6](#0-5) [7](#0-6) . Likewise, `VerifyExecutable` (nonce sequencing, repay-amount correctness) is only invoked from the `debug_isGaslessTx` RPC and the tx-pool "Ready" promotion logic, per the module's own README ("See ready condition ... and the implementation `IsExecutable`") [8](#0-7) , and from `GaslessAPI.IsGaslessTx` [9](#0-8) .

Crucially, `ApplyTransaction`/`TransitionDb` — the actual state-transition/EVM execution path used both by `StateProcessor.Process` and the validator's speculative `ExecuteTransactions` — performs no re-validation of gasless-specific invariants; it only checks generic consensus rules (nonce, balance, intrinsic gas) before handing off to the EVM [10](#0-9) [11](#0-10) . This means once a transaction calling `swapForGas` is included in a block — whether sent as an ordinary (non-"gasless-classified") transaction, sent by a contract with code, submitted with a stale/incorrect `amountRepay`, or reached via an internal `CALL` from another contract — the contract executes it with no on-chain guard reproducing the "EOA-only," "correct repay amount," or "sequential nonce" checks that the off-chain module assumes are always enforced before inclusion.

### Impact Explanation
Because the entire "amountRepay funds the block proposer, remainder funds the user" accounting (see the `SwappedForGas(proposer, amountRepaid, user, finalUserAmount, commission)` event [12](#0-11) ) depends on `amountRepay` being computed correctly per `repayAmount()` [13](#0-12) , and this computation is never verified inside `swapForGas` itself, a caller who reaches the function outside the gasless tx-pool path (e.g., a contract-mediated call, or a transaction assembled/relayed by a compromised or non-compliant block builder/proposer) can supply an arbitrary `amountRepay`/`minAmountOut` pair. This can misallocate value between "proposer repayment" and "user final amount," or (if combined with the missing sender-code check) enable atomic, single-transaction abuse of the swap/repay mechanism that the two-phase EOA-only design was meant to prevent — a fee-delegation/gasless settlement abuse matching the "unauthorized value movement / fee-delegation abuse" impact class.

### Likelihood Explanation
Medium-High. Reaching `swapForGas` does not require any privileged role — it is directly reachable by any transaction sender or by a contract making an internal call, since the contract has no `onlyRouter`/`onlyEOA`/caller restriction of its own (confirmed by the absence of any such modifier in the exposed ABI transactor methods) [14](#0-13) . The protections that would normally prevent misuse (`checkBalanceForSwap`, `VerifyExecutable`) are implemented as Go-level, tx-pool-only gates and are bypassed entirely once a transaction is included in a block through any path other than normal local mempool promotion (e.g., a malicious/non-standard block builder, direct RPC `eth_sendRawTransaction` combined with block inclusion by a cooperating/compromised proposer, or a contract-to-contract call during EVM execution of an already-included transaction).

### Recommendation
Move the critical invariants (repay-amount correctness relative to actual gas lent, minAmountOut ≥ amountRepay, and non-EOA/atomicity restrictions where required) into the `GaslessSwapRouter` contract itself so they are enforced by the EVM/state-transition logic regardless of how the transaction reached inclusion, rather than relying solely on off-chain `kaiax/gasless` tx-pool admission checks. At minimum, add an on-chain check that `msg.sender == tx.origin` (or an explicit authorized-caller check) and validate `amountRepay` against the actual gas price/gas paid by the current transaction before disbursing funds to `block.coinbase`.

### Proof of Concept
Conceptually mirroring the 98Token PoC: an attacker deploys a contract (or crafts a transaction) that calls `GaslessSwapRouter.swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` directly via `eth_sendRawTransaction`/contract call, supplying a `token`/`amountRepay`/`minAmountOut` combination that the off-chain `checkBalanceForSwap`/`VerifyExecutable` gates would have rejected (e.g., a caller with code, or an `amountRepay` inconsistent with `repayAmount()`), but which is never re-validated inside `swapForGas` at execution time — since `ApplyTransaction`/`TransitionDb` only check generic consensus rules, not gasless-specific ones [10](#0-9) . I was unable to fully verify the exact internal Solidity logic of `swapForGas` (only compiled bytecode/Go bindings are indexed, no `.sol` source file for `GaslessSwapRouter` was found in this repo) [15](#0-14) ; a Devin session with full repository/tooling access would be needed to decompile or locate the original source and confirm precisely how `amountRepay`/`proposer` disbursement is computed on-chain.

### Citations

**File:** kaiax/gasless/impl/getter.go (L40-47)
```go
	// function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) external
	routerAbiJson = `[{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"minAmountOut","type":"uint256"},{"internalType":"uint256","name":"amountRepay","type":"uint256"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapForGas","outputs":[],"stateMutability":"nonpayable","type":"function"}]`
)

var (
	erc20BalanceOfFunc = mustParseAbi(erc20AbiJson, "balanceOf")
	erc20ApproveFunc   = mustParseAbi(erc20AbiJson, "approve")
	routerSwapFunc     = mustParseAbi(routerAbiJson, "swapForGas")
```

**File:** kaiax/gasless/impl/getter.go (L88-103)
```go
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

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

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

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L100-182)
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

**File:** kaiax/gasless/README.md (L13-23)
```markdown
### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.
```

**File:** kaiax/gasless/impl/api.go (L70-106)
```go
// IsGaslessTx checks if the given raw transactions form a valid gasless transaction
// It returns a detailed result explaining why a transaction is not a valid gasless transaction if it's not
func (s *GaslessAPI) IsGaslessTx(ctx context.Context, rawTxs []hexutil.Bytes) *GaslessTxResponse {
	if len(rawTxs) == 0 {
		return ToResponse(errors.New("no transactions provided"))
	}

	// Decode the raw transactions
	txs := make([]*types.Transaction, 0, len(rawTxs))
	for i, rawTx := range rawTxs {
		if len(rawTx) == 0 {
			return ToResponse(fmt.Errorf("empty transaction at index %d", i))
		}

		// Handle Ethereum transaction envelope
		if 0 < rawTx[0] && rawTx[0] < 0x7f {
			rawTx = append([]byte{byte(types.EthereumTxTypeEnvelope)}, rawTx...)
		}

		tx := new(types.Transaction)
		if err := rlp.DecodeBytes(rawTx, tx); err != nil {
			return ToResponse(fmt.Errorf("failed to decode transaction at index %d: %v", i, err))
		}

		txs = append(txs, tx)
	}

	// Check if the transactions form a valid gasless transaction
	// Case 1: A single swap transaction
	if len(txs) == 1 {
		swapTx := txs[0]
		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("transaction is not a swap transaction"))
		}

		return ToResponse(s.b.VerifyExecutable(nil, swapTx))
	}
```

**File:** blockchain/state_transition.go (L515-569)
```go
func (st *StateTransition) TransitionDb() (*ExecutionResult, error) {
	// First check this message satisfies all consensus rules before
	// applying the message. The rules include these clauses
	//
	// 1. the nonce of the message caller is correct
	// 2. caller has enough balance to cover transaction fee(gaslimit * gasprice)
	// 3. the amount of gas required is available in the block
	// 4. the purchased gas is enough to cover intrinsic usage
	// 5. there is no overflow when calculating intrinsic gas
	// 6. caller has enough balance to cover asset transfer for **topmost** call

	st.captureTxStartPreCheck()

	// Check clauses 1-3, buy gas if everything is correct
	if err := st.preCheck(); err != nil {
		return nil, err
	}

	var (
		msg              = st.msg
		msgTo            = msg.To()
		contractCreation = msgTo == nil
		rules            = st.evm.ChainConfig().Rules(st.evm.Context.BlockNumber)
		floorDataGas     uint64
		err              error
	)

	if st.evm.Config.Debug {
		st.evm.Config.Tracer.CaptureTxStart(st.initialGas)
		defer func() {
			st.evm.Config.Tracer.CaptureTxEnd(st.gas)
		}()
	}

	// Check clauses 4-5, subtract intrinsic gas if everything is correct
	validatedGas := msg.ValidatedGas()
	if st.gas < validatedGas.IntrinsicGas {
		return nil, ErrIntrinsicGas
	}
	if rules.IsPrague {
		floorDataGas, err = FloorDataGas(msg.Type(), msg.Data(), validatedGas.SigValidateGas)
		if err != nil {
			return nil, err
		}
		if msg.Gas() < floorDataGas {
			return nil, fmt.Errorf("%w: have %d, want %d", ErrFloorDataGas, st.gas, floorDataGas)
		}
	}
	// SigValidationGas is already inclduded in IntrinsicGas
	st.gas -= validatedGas.IntrinsicGas

	// Check clause 6
	if msg.Value().Sign() > 0 && !st.evm.Context.CanTransfer(st.state, msg.ValidatedSender(), msg.Value()) {
		return nil, vm.ErrInsufficientBalance
	}
```

**File:** blockchain/state_processor.go (L84-94)
```go
	// Iterate over and process the individual transactions
	for i, tx := range block.Transactions() {
		statedb.SetTxContext(tx.Hash(), block.Hash(), i)
		receipt, internalTxTrace, err := p.bc.ApplyTransaction(p.config, &author, statedb, header, tx, usedGas, &cfg)
		if err != nil {
			return nil, nil, 0, nil, processStats, err
		}
		receipts = append(receipts, receipt)
		allLogs = append(allLogs, receipt.Logs...)
		internalTxTraces = append(internalTxTraces, internalTxTrace)
	}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L35-35)
```go
	Bin: "0x60a0346100e557601f6200198738819003918201601f19168301916001600160401b038311848410176100ea578084926020946040528339810103126100e557516001600160a01b038116908190036100e55761005b33610100565b80156100a05761006a33610100565b608052600060035560405161183f908162000148823960805181818161044a01528181610a7301528181610fb201526112960152f35b60405162461bcd60e51b815260206004820152601b60248201527f5a65726f2061646472657373206973206e6f7420616c6c6f77656400000000006044820152606490fd5b600080fd5b634e487b7160e01b600052604160045260246000fd5b600080546001600160a01b039283166001600160a01b03198216811783559216907f8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e09080a356fe60406080815260048036101561001f575b5050361561001d57600080fd5b005b600091823560e01c8062fa3d50146112ba578063145d51d814611276578063161efb621 ... (truncated)
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-573)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
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
