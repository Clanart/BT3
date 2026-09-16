### Title
Gasless swap repay-amount is validated only against caller-declared calldata, not against on-chain contract execution results, allowing proposer fee-lending drain - (File: kaiax/gasless/impl/getter.go)

### Summary
The POAP incident describes a minting/authorization-control failure where an off-chain/pool-side validator allowed unauthorized value to be issued because the checks were performed on attacker-controlled input rather than being enforced by the authoritative execution path. The Kaia analog is the `kaiax/gasless` module: the proposer (via `GetLendTxGenerator`) unconditionally funds (mints/loans) native KAIA to a swap sender based purely on statically decoded transaction fields, and the "repayment" check (`VerifyExecutable`/`repayAmount`) only verifies that the *declared* `amountRepay` field in the calldata matches a formula — it never verifies that the `GaslessSwapRouter` contract execution actually delivers that value back to the proposer/coinbase.

### Finding Description
`GaslessModule.GetLendTxGenerator` builds and signs, with the node's own key, a `LendTx` that unconditionally transfers `lendAmount(approveTxOrNil, swapTx)` KAIA from the proposer to the swap sender: [1](#0-0) 

This lend amount and the expected repayment are computed purely from the fee fields of the *submitted* approve/swap transactions: [2](#0-1) 

The only guard that a user must actually repay the lent amount is `VerifyExecutable`, which decodes the `swapForGas(...)` calldata via ABI unpacking and checks that the caller-supplied `amountRepay` argument equals the computed `repayAmount`: [3](#0-2) 

Critically, `amountRepay` here is a value taken from `tx.Data()` (attacker-controlled calldata), decoded by `decodeSwapTx`/`decodeFunctionCall`: [4](#0-3) 

The module's pre-admission balance check (`checkBalanceForSwap`) similarly only validates `AmountIn`/allowance/balance against ERC20 contract state and static comparisons of `minAmountOut`/`amountRepay`, but does not simulate execution of `swapForGas` to confirm the router contract actually enforces that exact repay value is transferred to the proposer: [5](#0-4) 

Because `VerifyExecutable` is a pool-level/pre-execution admission and bundling gate (used both in `ExtractTxBundles` to decide whether to include the lend transaction, and in `isReady`/`isApproveTxReady`/`isSwapTxReady` for promotion) rather than a state-transition-enforced invariant, the correctness of value return depends entirely on the `GaslessSwapRouter` contract's own internal enforcement of `amountRepay`. If that contract (whose Solidity source was not found in the indexed codebase — see below) has any code path where the declared `amountRepay` is not what is actually transferred to `block.coinbase`/proposer (e.g., a revert-then-partial-refund path, a rounding difference, a reentrancy/callback that alters state after the check, or a version mismatch between the `GaslessSwapRouter` binary ABI and the actual deployed bytecode), a swap sender can have the proposer unconditionally "mint"/lend KAIA to them up-front (`GetLendTxGenerator`) while the router under-delivers the repay amount, resulting in fee-delegation abuse/value theft from the block proposer — directly analogous to POAP's minting authorization bypass (a privileged issuance path trusting caller-supplied data instead of verifying the real state change).

### Impact Explanation
This maps to "fee or fee-delegation abuse" / "unauthorized value movement" explicitly in scope: the block proposer is a fee-delegation counterparty who funds every gasless-tx sender based only on calldata-derived arithmetic, not on a verified on-chain settlement. If the router doesn't perfectly enforce the declared repay amount (a condition that could not be fully verified because the `GaslessSwapRouter` Solidity source is not present in this index — only its ABI bindings were found: `contracts/bindings/kip247/GaslessSwapRouter.go`), an attacker sending arbitrary swap-tx calldata could drain proposer funds at scale across many blocks (each swap lends up to the sum of approve+swap tx fees). Severity is Medium-High class, gated on the router's actual enforcement.

### Likelihood Explanation
Any unprivileged transaction sender can craft a `GaslessApproveTx`/`GaslessSwapTx` pair and submit it via the public RPC/tx pool; no special privilege is required to reach `IsExecutable`/`GetLendTxGenerator`/`checkBalanceForSwap`. The trust boundary weakness (validating declared calldata instead of actual execution outcome) is a structural pattern, so likelihood of exploitability depends only on whether the router contract has any deviation between "declared amountRepay" and "actually transferred amountRepay" — which cannot be confirmed from the indexed Go/test code alone.

### Recommendation
- Verify (and if necessary enforce) that the `GaslessSwapRouter.swapForGas` implementation strictly transfers exactly `amountRepay` to `block.coinbase`/proposer atomically within the same call, with no code path (partial revert, fee-on-transfer token, callback) that can under-deliver.
- Consider adding an execution-level (state-transition) invariant check after the swap bundle executes — verifying the proposer's balance increased by the expected `repayAmount` — rather than relying solely on pre-execution static calldata validation in `VerifyExecutable`.
- Add regression/fuzz tests that simulate malicious or non-standard ERC20/router behavior (fee-on-transfer, reentrancy, partial repay) to confirm the lend/repay invariant holds under adversarial contract behavior.

### Proof of Concept
Not fully constructible from the indexed codebase because the `GaslessSwapRouter` contract's Solidity implementation (which is the ultimate enforcer of the repay invariant) is not available in this index — only Go bindings (`contracts/bindings/kip247/GaslessSwapRouter.go`) were found. A concrete PoC would require: (1) deploying/inspecting the actual `GaslessSwapRouter` bytecode to find a path where declared `amountRepay` isn't fully transferred to the proposer, (2) crafting an `approveTx`+`swapTx` pair with a `SwapArgs.AmountRepay` value that satisfies `VerifyExecutable`'s formula check in `kaiax/gasless/impl/getter.go:261-263` but which the router under-pays at execution time, and (3) observing the proposer's `LendTx` (funded via `GetLendTxGenerator`) being only partially recovered. This last step cannot be confirmed without the router's source, so this finding should be treated as **conditional/unverified** pending confirmation of the router contract's transfer-enforcement logic — flagging it here as the closest reachable analog to the POAP "unauthorized minting/issuance due to inadequately verified request" bug class.

### Citations

**File:** kaiax/gasless/impl/getter.go (L142-193)
```go
func decodeSwapTx(tx *types.Transaction, signer types.Signer) (args *SwapArgs, ok bool) {
	to, inputs, ok := decodeFunctionCall(tx, routerSwapFunc)
	if !ok {
		return nil, false
	}
	token, ok := inputs["token"].(common.Address)
	if !ok {
		return nil, false
	}
	amountIn, ok := inputs["amountIn"].(*big.Int)
	if !ok {
		return nil, false
	}
	minAmountOut, ok := inputs["minAmountOut"].(*big.Int)
	if !ok {
		return nil, false
	}
	amountRepay, ok := inputs["amountRepay"].(*big.Int)
	if !ok {
		return nil, false
	}
	deadline, ok := inputs["deadline"].(*big.Int)
	if !ok {
		return nil, false
	}
	from, err := types.Sender(signer, tx)
	if err != nil {
		return nil, false
	}
	return &SwapArgs{
		Sender:       from,
		Router:       to,
		Token:        token,
		AmountIn:     amountIn,
		MinAmountOut: minAmountOut,
		AmountRepay:  amountRepay,
		Deadline:     deadline,
	}, true
}

func decodeFunctionCall(tx *types.Transaction, method abi.Method) (common.Address, map[string]interface{}, bool) {
	if tx.Type() != types.TxTypeLegacyTransaction || // not legacy tx: unable to statically determine the max gas fee.
		tx.To() == nil || // not a contract call.
		len(tx.Data()) < 4 || // too short to be a contract call.
		!bytes.Equal(tx.Data()[:4], method.ID) { // not the target function.
		return common.Address{}, nil, false
	}

	inputs := make(map[string]interface{})
	err := method.Inputs.UnpackIntoMap(inputs, tx.Data()[4:])
	return *tx.To(), inputs, err == nil
}
```

**File:** kaiax/gasless/impl/getter.go (L195-266)
```go
// IsGaslessPattern checks following conditions:
// Ax. IsApproveTx conditions (if ApproveTx != nil)
// Sx. IsSwapTx conditions
// AP1. ApproveTx.from == SwapTx.from
// SP1. ApproveTx.to == SwapTx.token
// SP2. ApproveTx.amount >= SwapTx.amountIn
// SP3. ApproveTx.nonce+1 == SwapTx.nonce and Gasless transactions are head for nonce
// SP4. SwapTx.amountRepay = RepayAmount(ApproveTx, SwapTx)
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

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

**File:** kaiax/gasless/impl/getter.go (L268-313)
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
```

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
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
