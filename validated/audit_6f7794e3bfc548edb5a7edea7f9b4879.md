## Finding

### Title
Unauthenticated resource exhaustion via `debug_isGaslessTx` triggering multiple expensive EVM calls without gas payment or rate limiting - (File: kaiax/gasless/impl/api.go)

### Summary
The `GaslessModule` explicitly registers its `IsGaslessTx` / `GaslessInfo` methods under the `debug` namespace with `Public: true` [1](#0-0) , unlike every other `debug` namespace service in the codebase, which is registered with `Public: false` and typically gated by `IPCOnly` [2](#0-1) . Because `Public: true` causes the RPC server to register the method whenever the module whitelist is empty (the common default) or explicitly includes `debug`, `debug_isGaslessTx` is reachable by any unauthenticated public-RPC caller [3](#0-2) .

### Finding Description
`IsGaslessTx` accepts arbitrary attacker-supplied raw transaction bytes, performs cheap decoding/shape checks, and then calls `VerifyExecutable`, which in turn calls `checkBalanceForApprove`/`checkBalanceForSwap` [4](#0-3) . These functions execute multiple live EVM read-calls against attacker-controlled token/router addresses — `ERC20.BalanceOf`, `ERC20.Allowance`, and `GaslessSwapRouter.GetAmountIn` — each going through `BlockchainContractBackend` state execution [5](#0-4) . None of this requires a real signature-verified, gas-paid, pool-admitted transaction; the caller only needs to supply syntactically valid RLP bytes decoding to a swap/approve shaped call. There is no per-request cost accounting, no rate limiting specific to this endpoint, and no requirement that the token/router contracts be legitimate or cheap to execute — an attacker can point `Token`/`Router` at a contract with an expensive fallback/view function to multiply the CPU cost of each call, and repeat the request indefinitely since it is free and public.

This mirrors the reported bug class: a nominally "administrative"/privileged-sounding endpoint (`debug` namespace) that performs expensive backend computation (multiple EVM contract calls) for an unauthenticated caller without any resource/authorization gate prior to the expensive work, enabling repeated calls to exhaust node CPU/goroutine resources.

### Impact Explanation
Any public-RPC caller can repeatedly invoke `debug_isGaslessTx` with crafted approve/swap-shaped transactions pointing at attacker-deployed, computation-heavy token/router contracts, forcing the node to execute multiple EVM calls per request with no cost to the caller. Sustained or parallel invocation can degrade or exhaust RPC node resources (CPU, DB/state read throughput), affecting availability of the RPC endpoint for legitimate gasless/auction/public callers — a service degradation vector distinctly reachable through a normal, unauthenticated public RPC call.

### Likelihood Explanation
High reachability: the method is deliberately marked `Public: true` in a `debug`-namespace API (unlike all sibling debug APIs), so it is exposed whenever the `debug` module is enabled or no explicit module whitelist is configured — a common operational default. No signature, balance, nonce, or pool-admission checks gate the expensive path; only structural transaction decoding occurs first, which is trivially satisfied by any caller.

### Recommendation
- Change the `GaslessAPI` registration to `Public: false` (and/or `IPCOnly`), consistent with other `debug` namespace services, so it isn't exposed to arbitrary public callers by default.
- Add a computation/gas budget or timeout to `VerifyExecutable`/`checkBalanceForApprove`/`checkBalanceForSwap` EVM calls, independent of the caller-supplied token/router address.
- Add per-IP/per-caller rate limiting specific to `debug_isGaslessTx` similar to the peer-level bid rate limiter used in the auction module [6](#0-5) .

### Proof of Concept
1. Deploy an ERC20-like contract whose `balanceOf`/`allowance` (or a router whose `getAmountIn`) contains an expensive loop.
2. Craft an RLP-encoded "swap" transaction (per `decodeSwapTx`/`isSwapTx` shape) referencing that contract as `Token`/`Router`.
3. Repeatedly call `debug_isGaslessTx` over the public RPC endpoint with this raw transaction bytes; each call triggers `checkBalanceForSwap`'s three contract calls (`BalanceOf`, `Allowance`, `GetAmountIn`) at full EVM execution cost with no payment or gating [7](#0-6) .
4. Repeat concurrently from multiple connections to degrade node responsiveness.

### Citations

**File:** kaiax/gasless/impl/api.go (L31-40)
```go
func (b *GaslessModule) APIs() []rpc.API {
	return []rpc.API{
		{
			Namespace: "debug",
			Version:   "1.0",
			Service:   NewGaslessAPI(b),
			Public:    true,
		},
	}
}
```

**File:** kaiax/gasless/impl/api.go (L97-123)
```go
	// Check if the transactions form a valid gasless transaction
	// Case 1: A single swap transaction
	if len(txs) == 1 {
		swapTx := txs[0]
		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("transaction is not a swap transaction"))
		}

		return ToResponse(s.b.VerifyExecutable(nil, swapTx))
	}

	// Case 2: An approve transaction followed by a swap transaction
	if len(txs) == 2 {
		approveTx := txs[0]
		swapTx := txs[1]

		if !s.b.IsApproveTx(approveTx) {
			return ToResponse(errors.New("first transaction is not an approve transaction"))
		}

		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("second transaction is not a swap transaction"))
		}

		err := s.b.VerifyExecutable(approveTx, swapTx)
		return ToResponse(err)
	}
```

**File:** node/cn/backend.go (L723-740)
```go
			Service:   api.NewDebugAPI(s.APIBackend),
			Public:    false,
		}, {
			Namespace: "kaia",
			Version:   "1.0",
			Service:   kaiaAccountAPI,
			Public:    true,
		}, {
			Namespace: "personal",
			Version:   "1.0",
			Service:   api.NewPersonalAPI(s.APIBackend, nonceLock),
			Public:    false,
		}, {
			Namespace: "debug",
			Version:   "1.0",
			Service:   api.NewDebugUtilAPI(s.APIBackend),
			Public:    false,
			IPCOnly:   s.config.DisableUnsafeDebug,
```

**File:** networks/rpc/endpoints.go (L42-53)
```go
	for _, api := range apis {
		if api.Namespace == "klay" {
			api.Namespace = "kaia"
		}

		if !api.IPCOnly && (whitelist[api.Namespace] || (len(whitelist) == 0 && api.Public)) {
			if err := handler.RegisterName(api.Namespace, api.Service); err != nil {
				return nil, nil, err
			}
			logger.Debug("HTTP registered", "namespace", api.Namespace)
		}
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L74-180)
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

```

**File:** kaiax/auction/impl/bid_pool.go (L439-455)
```go
// checkRateLimit checks if the peer is within rate limit
func (bp *BidPool) checkRateLimit(peerID string) bool {
	bp.peerRateLimiterMu.Lock()
	defer bp.peerRateLimiterMu.Unlock()

	limiter, exists := bp.peerRateLimiter.Get(peerID)
	if !exists {
		// Create new rate limiter for this peer
		// Use burst equal to the rate limit (we only use rate limit, not the burst)
		limiter = rate.NewLimiter(rate.Limit(bidsPerSecondPerPeer), bidsPerSecondPerPeer)
		bp.peerRateLimiter.Add(peerID, limiter)
	}

	// It'll simply discard the bid if the rate limit is exceeded
	// We don't need to reserve for a bid here because the original bid will be sent from auctioneer through different channel (see #api.SubmitBid)
	return limiter.(*rate.Limiter).Allow()
}
```
