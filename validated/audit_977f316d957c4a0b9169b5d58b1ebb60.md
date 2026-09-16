## Analysis: Analog of DODOApprove-style Unlimited Approval Risk in Kaia's Gasless Module

### Title
Gasless module mandates unlimited (MaxUint256) ERC20 approval to a registry-controlled, external `GaslessSwapRouter` contract, exposing users to full-balance theft — (File: `kaiax/gasless/impl/getter.go`)

### Summary
Kaia's `kaiax/gasless` module (KIP-247) requires users to grant an **unlimited** ERC20 allowance to the `GaslessSwapRouter` contract as a precondition for a valid `GaslessApproveTx`. The router's address is not fixed in the audited/core repo — it is fetched dynamically from the on-chain `Registry` system contract, and its actual Solidity implementation lives outside this repository (only ABI/bytecode bindings are present). This mirrors the DODOApprove pattern: users are forced into an unlimited allowance to a contract whose logic and governance are outside the scope being reviewed, and a single one-time approval is exploitable to drain their full token balance, not just the intended swap amount.

### Finding Description
`IsApproveTx`/`isApproveTx` enforces condition A4 — the approved amount **must equal `MaxUint256`** — as a strict requirement for a transaction to qualify as part of a gasless bundle: [1](#0-0) 

The `spender` that receives this unlimited allowance is `g.swapRouter`, which is not a constant, audited, immutable contract — it is read at every block from the `Registry` system contract via `MultiCallGaslessInfo`, meaning its address can change over the chain's lifetime (via governance/registry `register` calls): [2](#0-1) [3](#0-2) 

The transaction-pool level balance check for a swap only validates that the *current* allowance is sufficient to cover the specific `amountIn` of that swap: [4](#0-3) 

Crucially, this check is a pool-admission heuristic, not an on-chain enforcement — once the approve transaction is mined, the router contract holds a standing `MaxUint256` allowance over the user's balance. Any future `transferFrom` call made directly by that contract (or by anyone who controls it, e.g. through an owner-privileged function, an upgrade, or a bug) can pull the user's **entire token balance**, not merely the amount tied to the paired swap. This is confirmed by the `GaslessSwapRouter` contract exposing owner-gated administrative functions (`addToken`, `removeToken`, `claimCommission`, `transferOwnership`) whose full implementation is not part of this repository — only bindings/bytecode are present: [5](#0-4) 

Because the actual router logic is external to the audited protocol scope (similar to DODOApprove not being part of the DODO margin trading contracts), a compromised owner key, a router logic bug, or a malicious registry update pointing `GaslessSwapRouterName` to an attacker-controlled address would let that entity call `transferFrom` on every user who has ever completed a `GaslessApproveTx`, draining full balances rather than the bounded swap amount.

### Impact Explanation
Every user who completes a `GaslessApproveTx` (a normal, incentivized flow encouraged by the gasless UX) grants an irrevocable, unlimited allowance to a contract address that is:
1. Not hardcoded/immutable — controlled by the on-chain `Registry`.
2. Not part of the reviewed core repo — its Solidity source isn't present, only bindings/bytecode.

If that router is compromised (owner key leak, logic bug, or a governance-directed registry swap to a malicious contract), an attacker can call `transferFrom` to drain the **entire token balance** of any user who ever approved, far beyond the amount needed for their gasless swap. This is a direct, protocol-mandated unauthorized value movement vector, matching the severity class of the original DODOApprove finding.

### Likelihood Explanation
Every gasless swap requires the `MaxUint256` approval by protocol design (`args.Amount.Cmp(abi.MaxUint256) == 0`), so exposure is universal among gasless users, not an edge case. The trigger requires either compromise of the router's admin/owner key or a malicious/erroneous registry update — a governance-adjacent, plausible attack surface given `swapRouter` is resolved fresh from `Registry` every block.

### Recommendation
- Avoid mandating `MaxUint256` approvals; require approve amount to be bounded to the specific swap's `amountIn` (or a capped, short-lived allowance) so a compromised router can only ever pull the intended amount.
- Ensure the `GaslessSwapRouter` contract source is part of the audited/core protocol, with immutable, non-upgradeable pull-authorization logic, and strict access control over any registry update that can silently redirect the trusted `swapRouter` address.

### Proof of Concept
1. User Alice sends a `GaslessApproveTx` approving `TestToken.approve(swapRouter, MaxUint256)`, satisfying condition A4 in `isApproveTx`.
2. This transaction is promoted and included because `IsExecutable`/`VerifyExecutable` only check that the *paired* swap's `amountIn` is covered by the allowance, not that the allowance is capped: [6](#0-5) 
3. After the swap settles, Alice's full token balance remains approved to `swapRouter` at `MaxUint256`.
4. If `swapRouter`'s owner key is compromised, or `Registry.register(GaslessSwapRouterName, maliciousAddr, ...)` is executed (as exercised in tests updating `g.swapRouter` via `RegistryMockTransactor.Register`): [7](#0-6) 
   the malicious/compromised contract can call `TestToken.transferFrom(alice, attacker, aliceFullBalance)` at any later time, independent of the gasless module's swap-specific balance checks, since ERC20 `transferFrom` execution is not restricted by the kaiax gasless tx-pool logic once mined on-chain.

### Citations

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

**File:** kaiax/gasless/impl/getter.go (L211-249)
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

**File:** kaiax/gasless/impl/getter.go (L369-389)
```go
func getGaslessInfo(bc backends.BlockChainForCaller, header *types.Header) (common.Address, []common.Address, error) {
	statedb, err := bc.StateAt(header.Root)
	if err != nil {
		return common.Address{}, nil, err
	}

	// If Registry is not installed, do not query GaslessSwapRouter contract.
	if statedb.GetCode(system.RegistryAddr) == nil || bc.Config().IsRandaoForkBlockParent(header.Number) {
		return common.Address{}, nil, nil
	}

	caller, err := system.NewMultiCallContractCaller(statedb, bc, header)
	if err != nil {
		return common.Address{}, nil, err
	}

	opts := &bind.CallOpts{BlockNumber: header.Number}
	info, err := caller.MultiCallGaslessInfo(opts)

	return info.Gsr, info.Tokens, err
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L151-163)
```go
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
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L484-531)
```go
// AddToken is a paid mutator transaction binding the contract method 0xc6e85b3b.
//
// Solidity: function addToken(address token, address factory, address router) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) AddToken(token common.Address, factory common.Address, router common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.AddToken(&_GaslessSwapRouter.TransactOpts, token, factory, router)
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) ClaimCommission(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "claimCommission")
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) ClaimCommission() (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.ClaimCommission(&_GaslessSwapRouter.TransactOpts)
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) ClaimCommission() (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.ClaimCommission(&_GaslessSwapRouter.TransactOpts)
}

// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) RemoveToken(opts *bind.TransactOpts, token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "removeToken", token)
}

// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) RemoveToken(token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.RemoveToken(&_GaslessSwapRouter.TransactOpts, token)
}

// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) RemoveToken(token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.RemoveToken(&_GaslessSwapRouter.TransactOpts, token)
}
```

**File:** kaiax/gasless/impl/execution_test.go (L118-142)
```go
		"update gsr address": {
			func() *GaslessModule {
				g := NewGaslessModule()
				dbm := database.NewMemoryDBManager()
				backend := backends.NewSimulatedBackendWithDatabase(dbm, alloc, testChainConfig)
				err := g.Init(&InitOpts{
					ChainConfig:   testChainConfig,
					GaslessConfig: testGaslessConfig,
					NodeKey:       nodekey,
					Chain:         backend.BlockChain(),
					NodeType:      common.ENDPOINTNODE,
				})
				require.NoError(t, err)

				sender := bind.NewKeyedTransactor(senderkey)
				contract, _ := contracts.NewRegistryMockTransactor(system.RegistryAddr, backend)
				contract.Register(sender, GaslessSwapRouterName, anotherGSR, big.NewInt(2))

				backend.Commit()
				backend.Commit()
				return g
			},
			anotherGSR,
			[]common.Address{dummyTokenAddress1},
		},
```
