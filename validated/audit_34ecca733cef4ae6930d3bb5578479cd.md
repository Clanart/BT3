### Title
Registry-controlled `GaslessSwapRouter` address receives max-uint token allowance from users, enabling fund drain if the registered router is malicious — (File: kaiax/gasless/impl/getter.go)

### Summary
The Kaia gasless module (KIP-247) trusts whatever address is currently registered under `GaslessSwapRouterName` in the system `Registry` as the sole recipient of user-granted `MaxUint256` ERC-20 allowances. If the Registry admission for this name is ever pointed to a malicious/compromised contract, every user who performs the standard gasless "approve + swap" flow grants that contract unlimited spending rights over their token, exactly mirroring the Mycelium `Vault.sol` "max allowance to plugin" bug class.

### Finding Description
`GaslessModule.updateAddresses` reads the current `GaslessSwapRouter` address for each block from the system Registry via `getGaslessInfo`, and unconditionally sets it as the trusted `g.swapRouter`: [1](#0-0) 

`getGaslessInfo` fetches this address purely from Registry state (`system.RegistryAddr` / `MultiCallGaslessInfo`), with no additional validation of the router's bytecode, ownership, or behavior: [2](#0-1) 

The module then classifies any `approve(spender, amount)` transaction as a valid `GaslessApproveTx` as long as `spender == g.swapRouter` and `amount == MaxUint256`: [3](#0-2) 

The end-to-end test confirms this Registry-driven trust model — the router address is simply registered once via `registry.Register(..., GaslessSwapRouterName, gsrAddr, targetBlockNum)` and thereafter every user's `ApproveTx` grants `abi.MaxUint256` allowance to that exact address: [4](#0-3) [5](#0-4) 

Because the balance/allowance check for the subsequent `SwapTx` only verifies `allowance(sender, swapRouter) >= amountIn` and does not otherwise constrain what code the router executes, the registered router contract holds unlimited spending power over every approving user's whitelisted token: [6](#0-5) 

This is structurally identical to the reported bug class: an admin-controlled binding (Registry entry for `GaslessSwapRouterName`, analogous to Vault's "plugin" registration) is granted `MaxUint256` allowance by end users, so if that admin is compromised or the registered address is swapped to a malicious contract, all approved user funds for supported tokens become drainable in a single `transferFrom` call by the malicious contract — no allowance-scoping or per-swap approval is enforced.

### Impact Explanation
Any token for which users perform the gasless approve flow is exposed to unlimited allowance to the currently-registered `GaslessSwapRouter`. A malicious or compromised registration lets the attacker's contract call `transferFrom(user, attacker, balance)` on every approving user's whitelisted token, directly draining user funds — a concrete unauthorized value movement matching the "gasless settlement theft" category.

### Likelihood Explanation
Exploitation requires control over the Registry admission for `GaslessSwapRouterName` (a governance/admin-controlled system parameter) plus at least one user completing the ordinary, publicly reachable gasless approve+swap flow — both are within scope as "governance parameters" and "gasless module" reachable paths, not requiring any malicious-node/consensus compromise.

### Recommendation
- Do not let the gasless module rely solely on Registry registration to imply unconditional trust for unlimited allowances; consider bounding approvals to the exact `amountIn` needed per swap rather than requiring/accepting `MaxUint256`.
- Add a cooldown/verification window and multi-party control (e.g., governance vote delay already used for other Registry entries) specifically for `GaslessSwapRouterName` changes, and emit/verify router code hash checks before trusting a newly registered router address.
- Consider using `safeIncreaseAllowance`/per-swap `safeApprove` patterns from `contracts/libs/openzeppelin-contracts-v2/contracts/token/ERC20/SafeERC20.sol` instead of infinite approval semantics baked into `IsApproveTx`'s `A4` check.

### Proof of Concept
1. Registry admin (compromised or malicious) calls `registry.Register(GaslessSwapRouterName, maliciousRouter, targetBlock)`.
2. At `targetBlock`, `GaslessModule.updateAddresses` (kaiax/gasless/impl/getter.go:315) updates `g.swapRouter = maliciousRouter`.
3. A wallet/user, following the standard KIP-247 flow, sends `token.approve(maliciousRouter, MaxUint256)` — accepted as a valid `GaslessApproveTx` per `isApproveTx` (kaiax/gasless/impl/getter.go:79-86).
4. `maliciousRouter` (deployed by the same attacker) calls `token.transferFrom(user, attacker, token.balanceOf(user))`, draining the user's full balance — no code path in `checkBalanceForSwap` (kaiax/gasless/impl/tx_pool.go:144-172) prevents this because it only checks `allowance >= amountIn` against whatever `g.swapRouter` currently is.

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

**File:** kaiax/gasless/impl/getter.go (L315-341)
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

**File:** tests/gasless_test.go (L107-121)
```go
	/* ------------- Register GaslessSwapRouter address in Registry ------------- */
	// send register tx
	targetBlockNum := new(big.Int).Add(node.BlockChain().CurrentHeader().Number, big.NewInt(4))
	registry, err := kip149contract.NewRegistry(system.RegistryAddr, transactor)
	if err != nil {
		t.Fatal(err)
	}
	registerTx, err := registry.Register(bind.NewKeyedTransactor(owner.Keys[0]), gaslessImpl.GaslessSwapRouterName, gsrAddr, targetBlockNum)
	if err != nil {
		t.Fatal(err)
	}
	registerTxReceipt := waitReceipt(chain, registerTx.Hash())
	if registerTxReceipt == nil || registerTxReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to registor GaslessSwapRouter address")
	}
```

**File:** tests/gasless_test.go (L194-199)
```go
	// success send normal approveTx
	approveTx, err := sendApproveTx(t, testTokenContract, accounts[0], gsrAddr, abi.MaxUint256)
	if err != nil {
		t.Fatal(err)
	}
	accounts[0].Nonce += 1
```

**File:** kaiax/gasless/impl/tx_pool.go (L144-172)
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
```
