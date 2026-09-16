### Title
Stale cached `swapRouter`/allowed-token set lets gasless tx-pool validation diverge from actual on-chain `GaslessSwapRouter` state after a Registry update - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module caches the KIP-247 `GaslessSwapRouter` address and the allowed-token set in memory (`g.swapRouter`, `g.allowedTokens`) and only refreshes them once per block, in `PostInsertBlock` [1](#0-0) , which calls `updateAddresses` [2](#0-1) . Meanwhile, transaction-pool admission logic (`IsApproveTx`, `IsSwapTx`, `checkBalanceForSwap`, used via `IsModuleTx`/`GetCheckBalance`) validates incoming approve/swap transactions strictly against this cached copy rather than fetching the router/allowed-tokens live from the `Registry`/`AddressBookV2` at validation time [3](#0-2) [4](#0-3) . This mirrors the RabbitHoleReceipt bug class: one component (`QuestFactory`, here the tx-pool gate) holds an address that can be changed by governance, while a dependent component (`Quest`, here the on-chain `swapForGas`/router execution path) may already reflect the new address, or vice versa — causing the two paths to disagree about which router/tokens are authorized.

### Finding Description
`g.swapRouter` and `g.allowedTokens` are populated by `updateAddresses`, which is only invoked at module `Init` and once per `PostInsertBlock` [5](#0-4) [1](#0-0) . Between block N's insertion and block N+1's insertion, every transaction that the pool receives is checked with this single per-block snapshot:

- `isApproveTx`/`isSwapTx` gate whether a tx is recognized as gasless using `g.swapRouter == args.Spender`/`args.Router` [3](#0-2) .
- `checkBalanceForSwap` uses the same cached `swapRouter` to call `GetAmountIn` on the router contract and to check ERC-20 `Allowance(sender, swapRouter)` [6](#0-5) .

If a governance/registry action (e.g. `Registry` upgrade of `GaslessSwapRouter`, reachable to whoever controls the Registry — but more importantly, this is a state-divergence issue that can be triggered organically at every chain-reorg or fork boundary) changes the router or the allowed-token list, the txpool validation logic across CN/PN nodes can diverge for a window of one block: nodes that already processed the new block have updated cache; nodes that haven't yet call `PostInsertBlock` still validate against the old router. Because the lend/repay accounting in `GetLendTxGenerator`/`lendAmount`/`repayAmount` is computed purely from tx fee fields, not from what router actually executes on-chain [7](#0-6) , a transaction that is pool-admitted using the stale `GetAmountIn` quote from the old router can be executed on-chain against a different, currently-registered router (via the KIP-247 contract's own address resolution) with a different exchange rate. This is analogous to the `Quest`/`QuestFactory` bug: the pool's admission decision and the runtime execution consult the router address/quote from two different points in time, so the “amountIn is sufficient” check that gates pool admission and gas-lending is not guaranteed to reflect the router actually invoked at execution.

### Impact Explanation
This is a state-divergence / fee-delegation (gasless) settlement risk: the proposer (acting as a fee-delegation lender via `LendTxGenerator`) advances gas funds based on a balance/amount check performed against a stale cached swap-router quote [4](#0-3) , while actual swap settlement occurs against whatever router the swap-router bytecode/registry directs at execution time. A mismatch here can result in the proposer's lent gas not being fully repaid by the actual swap output, or honest nodes making different pool-admission decisions for the same transaction depending on whether they've already run `PostInsertBlock` for the block containing the router change — a form of state divergence in mempool acceptance across otherwise-honest nodes. This satisfies "reward/fee-delegation abuse" and "state divergence between honest nodes" categories.

### Likelihood Explanation
Likelihood is constrained by how often the `GaslessSwapRouter` registry entry or allowed-token set actually changes (a governance-controlled event), but the divergence window is deterministic and occurs on every such change, not requiring any attacker-controlled timing beyond submitting an approve/swap tx during the update block boundary. No special privilege is needed by the transaction sender — a normal gasless swap participant's transaction is what gets validated/settled inconsistently.

### Recommendation
Do not rely purely on a once-per-block memory cache for the security-critical `swapRouter` comparison used in balance/amount checks. Either (a) re-read the router address from the `Registry`/`MultiCall` contract using the state at validation time consistently with the state used for execution, or (b) have `checkBalanceForSwap` and `isApproveTx`/`isSwapTx` explicitly bind to the router address that will actually be used during execution (e.g., resolve it once per validated block and reject/re-validate transactions if the router changes before execution, similar to how `Quest.getRabbitholeReceiptContract()` was recommended to source the receipt contract directly from the immutable reference rather than a possibly-stale factory-level field).

### Proof of Concept
1. Governance updates the `GaslessSwapRouter` entry in the `Registry` contract in block N (a normal, permitted governance/registry transaction).
2. A CN node's txpool has not yet called `PostInsertBlock` for block N (e.g., during processing lag or right at the boundary), so `g.swapRouter` still holds the old router address.
3. A user submits a `GaslessApproveTx`/`GaslessSwapTx` referencing the old router as `Spender`/`Router`. `isApproveTx`/`isSwapTx` accept it because they compare against the stale `g.swapRouter` [3](#0-2) .
4. `checkBalanceForSwap` calls `GetAmountIn` and `Allowance` against the old router address, computing an outdated required `amountIn`/allowance check [6](#0-5) .
5. The proposer generates a `LendTx` funding the gas based on this validation [7](#0-6) , while actual on-chain execution of the swap resolves the router differently (per the updated Registry), producing a settlement mismatch between the amount validated for lending and the amount actually repayable — reachable purely from a public gasless-swap submission crossing a router-update block boundary.

**Uncertainty note:** I could not verify from the indexed files exactly how the KIP-247 `GaslessSwapRouter`/`swapForGas` on-chain execution path resolves its own router identity (whether it self-references or is looked up fresh from the Registry at call time), nor how `PostReset`/mempool re-validation interacts with `PostInsertBlock` timing across nodes. Confirming the precise execution-time address resolution would require reviewing the `contracts/system_contracts` Registry/KIP-247 Solidity sources and the `kaiax/gasless/impl/tx_pool.go PostReset` logic in full, which were not fully covered by the available index snippets.

### Citations

**File:** kaiax/gasless/impl/execution.go (L26-32)
```go
func (g *GaslessModule) PostInsertBlock(block *types.Block) error {
	currentState, err := g.Chain.StateAt(block.Header().Root)
	if err != nil {
		return err
	}
	g.setCurrentState(currentState)
	return g.updateAddresses(block.Header())
```

**File:** kaiax/gasless/impl/getter.go (L79-103)
```go
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

**File:** kaiax/gasless/impl/getter.go (L315-344)
```go
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L107-182)
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

**File:** kaiax/gasless/impl/init.go (L74-98)
```go
func (g *GaslessModule) Init(opts *InitOpts) error {
	if opts == nil || opts.ChainConfig == nil || opts.GaslessConfig == nil || opts.NodeKey == nil || opts.Chain == nil {
		return ErrInitUnexpectedNil
	}

	g.InitOpts = *opts
	g.signer = types.LatestSignerForChainID(g.ChainConfig.ChainID)
	currentState, err := g.Chain.State()
	if err != nil {
		return err
	}
	g.setCurrentState(currentState)

	// Disable module if CN (lender) does not have sufficient balance
	if g.NodeType == common.CONSENSUSNODE {
		nodeAddr := crypto.PubkeyToAddress(opts.NodeKey.PublicKey)
		balance := g.getCurrentStateBalance(nodeAddr)
		if balance.Cmp(GaslessLenderMinBal) < 0 {
			g.GaslessConfig.Disable = true
			logger.Warn("disabling gasless module due to insufficient balance", "node", nodeAddr.Hex(), "balance", balance.String())
		}
	}

	return g.updateAddresses(g.Chain.CurrentBlock().Header())
}
```
