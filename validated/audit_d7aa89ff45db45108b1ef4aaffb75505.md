### Title
Gasless swap bundle allows proposer fund drain via fee-on-transfer / non-standard ERC20 tokens in `GaslessModule` — ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module lends KAIA gas fees to a user via a proposer-funded `LendTx` before the corresponding `GaslessApproveTx`/`GaslessSwapTx` executes and repays the proposer. The mempool-level admission checks that gate this lending (`checkBalanceForApprove`/`checkBalanceForSwap`) rely on the token's own `balanceOf`/`allowance` view calls rather than verifying an actual, standard-conforming token transfer. Analogous to the Cally fee-on-transfer bug (where the contract recorded a nominal amount instead of the actually-received amount), a fee-on-transfer, deflationary, or otherwise non-standard/malicious ERC20 registered as an allowed gasless token can cause the actually-transferred `amountIn` during `swapForGas` to be less than what was validated/expected off-chain, while the proposer has already irreversibly paid out the `LendTx` value.

### Finding Description
`GaslessModule.GetLendTxGenerator` unconditionally creates a `LendTx` that sends `lendAmount(approveTxOrNil, swapTx)` KAIA from the block proposer to the user, based purely on declared gas fees of the approve/swap transactions: [1](#0-0) [2](#0-1) 

This `LendTx` is prepended to the bundle `[LendTxGenerator, ApproveTx, SwapTx]` and included in block building: [3](#0-2) 

The only pre-admission validation performed on the token side is `checkBalanceForApprove`/`checkBalanceForSwap`, which query `balanceOf`/`allowance` directly on the (attacker-supplied) ERC20 `token` contract: [4](#0-3) [5](#0-4) 

These checks assume the token behaves like a standard ERC20 (transferred amount == declared amount, and `balanceOf`/`allowance` accurately predict transfer outcomes). By default `AllowedTokens` is `nil`, meaning **all tokens are allowed** unless the node operator explicitly configures an allowlist: [6](#0-5) 

This mirrors exactly the Cally root cause: the system records/validates a nominal `amountIn`/balance figure instead of verifying the actual balance delta after transfer, and any actor who can get a non-standard token accepted (fee-on-transfer, deflationary, callback/rebasing, or a token whose `balanceOf`/`allowance` lie relative to actual transfer behavior) can desynchronize the value the module assumes moves versus what actually moves in `swapForGas`.

### Impact Explanation
Because the `LendTx` (proposer → user KAIA payment) is generated and included in the block based only on declared/mempool-time gas-fee arithmetic — independent of whether the subsequent `ApproveTx`/`SwapTx` actually deliver the promised token value to the `GaslessSwapRouter` — a user/attacker can register or exploit a fee-on-transfer/malicious ERC20 as the swap token. The `LendTx` unconditionally pays out KAIA from the proposer; if the actual token transfer inside `swapForGas` delivers less value than validated by the naive `balanceOf`/`allowance` checks (fee-on-transfer skimming, or a malicious token that reports fake balances/allowances), the router either cannot fully repay the proposer (partial or failed repayment) or the swap reverts after the `LendTx` has already unconditionally transferred value to the user. This results in unauthorized value movement / fee-delegation abuse: the proposer (fee-delegation counterparty in this gasless flow) loses KAIA while the attacker is guaranteed to receive it, matching the "loss of value from the contract, used as a conduit to generate income" pattern described in the report. Severity is Medium: it requires getting a non-standard token accepted for gasless use and is bounded per-transaction by `params.TxGas`/lend amount and repeatable per gasless bundle, consistent with the Medium classification given in the original finding (bounded, recoverable value leak rather than unbounded drain).

### Likelihood Explanation
Reachable by any unprivileged transaction sender: an attacker only needs to submit a `GaslessApproveTx` + `GaslessSwapTx` pair referencing a token contract they control (or a known fee-on-transfer token) as long as it is in `allowedTokens` — which is **all tokens by default** per `DefaultGaslessConfig` (`AllowedTokens: nil`). No special privileges (validator, node operator, etc.) are required; the `IsExecutable`/`VerifyExecutable` gating only checks amount relationships and nonce/deadline conditions, not actual token-transfer fidelity: [7](#0-6) 

### Recommendation
- Do not treat `AllowedTokens: nil` as "allow all"; require an explicit, curated allowlist of vetted standard-compliant ERC20 tokens for gasless swaps.
- In `checkBalanceForApprove`/`checkBalanceForSwap`, do not rely solely on `balanceOf`/`allowance` view calls; where feasible, simulate/verify that the actual balance delta of the `GaslessSwapRouter` after `transferFrom` matches the declared `amountIn` before permitting bundle inclusion.
- Ensure `GetLendTxGenerator`'s KAIA payout is only finalized/settled atomically with successful, value-accurate repayment from `swapForGas`, e.g., by validating that repayment succeeded before considering the bundle valid for future re-inclusion, and by monitoring/reverting proposer exposure when repayment shortfalls are detected.

### Proof of Concept
1. Node operator runs with default gasless config (`AllowedTokensFlag` default `"all"`, `DefaultGaslessConfig.AllowedTokens = nil`), so any ERC20 is accepted as a gasless swap token: [8](#0-7) 
2. Attacker deploys a fee-on-transfer ERC20 (or an ERC20 whose `balanceOf`/`allowance` return favorable values, but `transferFrom` delivers less than `amountIn` to the router), then adds it via `AddToken` to the `GaslessSwapRouter` as in the test flow `addTokenTx := gsrContract.AddToken(...)`: [9](#0-8) 
3. Attacker submits `GaslessApproveTx` (approve router for `amountIn`) then `GaslessSwapTx(token, amountIn, minAmountOut, amountRepay, deadline)` with a valid-looking `amountIn` that passes `checkBalanceForApprove`/`checkBalanceForSwap` (balance/allowance appear sufficient) as in `sendApproveTx`/`sendSwapTx`: [10](#0-9) 
4. The block proposer's `GetLendTxGenerator` computes `lendAmount` purely from `approveTx.Fee() + swapTx.Fee()` and unconditionally transfers this KAIA to the attacker's address before/along with the bundle: [11](#0-10) 
5. During on-chain execution of `swapForGas`, the fee-on-transfer token delivers less than `amountIn` to the router, causing insufficient proceeds to fully repay `amountRepay` to the proposer (or causing the swap to revert), while the `LendTx` KAIA transfer to the attacker has already occurred and cannot be clawed back — net value loss to the proposer.

### Citations

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

**File:** kaiax/gasless/impl/builder.go (L28-72)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L74-100)
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L144-173)
```go
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
```

**File:** kaiax/gasless/config.go (L26-33)
```go
var (
	AllowedTokensFlag = &cli.StringSliceFlag{
		Name:     "gasless.allowed-tokens",
		Usage:    "allow token addresses for gasless module, allow all tokens if all",
		Value:    cli.NewStringSlice("all"),
		Aliases:  []string{"kaiax.module.gasless.allowed-tokens"},
		Category: "KAIAX",
	}
```

**File:** kaiax/gasless/config.go (L71-88)
```go
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
```

**File:** tests/gasless_test.go (L462-473)
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
```

**File:** tests/gasless_test.go (L476-498)
```go
func sendApproveTx(t *testing.T, testTokenContract *testingGaslessContracts.TestToken, owner *TestAccountType, gsrAddr common.Address, amount *big.Int) (*types.Transaction, error) {
	optsForApprove := bind.NewKeyedTransactor(owner.Keys[0])
	optsForApprove.GasLimit = 300000
	optsForApprove.Nonce = big.NewInt(int64(owner.Nonce))
	approveTx, err := testTokenContract.Approve(optsForApprove, gsrAddr, amount)
	if err != nil {
		return nil, err
	}
	t.Log("approveTxHash", approveTx.Hash().Hex())
	return approveTx, nil
}

func sendSwapTx(t *testing.T, gsrContract *gaslessContract.GaslessSwapRouter, owner *TestAccountType, testTokenAddr common.Address, swapAmmount *big.Int, minAmountOut *big.Int, ammontRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	optsForSwap := bind.NewKeyedTransactor(owner.Keys[0])
	optsForSwap.GasLimit = 300000
	optsForSwap.Nonce = big.NewInt(int64(owner.Nonce))
	swapTx, err := gsrContract.SwapForGas(optsForSwap, testTokenAddr, swapAmmount, minAmountOut, ammontRepay, deadline)
	if err != nil {
		return nil, err
	}
	t.Log("swapTxHash", swapTx.Hash().Hex())
	return swapTx, nil
}
```
