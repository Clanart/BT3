### Title
Gasless `swapForGas` deadline is enforced only by the client's mempool admission check, not by the on-chain `GaslessSwapRouter` contract - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `MeritDutchAuction.mint` report describes a class of bug where a transaction lacks an authoritative, execution-time deadline check, allowing it to be mined arbitrarily far in the future at conditions the sender never agreed to. Kaia's gasless-swap feature (`GaslessSwapRouter.swapForGas`) has an analogous gap: the `deadline` parameter is validated only by the kaiax `gasless` module at tx-pool admission time, not by the on-chain contract logic itself.

### Finding Description
`SwapArgs.Deadline` is decoded from the transaction calldata and checked in `checkBalanceForSwap`: [1](#0-0) 

This function is only reachable through `GetCheckBalance`, which is invoked by the tx-pool's `CheckBalance` hook when a gasless transaction is submitted/validated by the local node's mempool: [2](#0-1) 

The corresponding error string observed in the integration test (`"insufficient deadline: deadline=1"`) is produced by this Go code path, not by an EVM revert originating from the `GaslessSwapRouter` contract itself: [3](#0-2) 

I was not able to locate the actual `GaslessSwapRouter.sol` source in the index (only the compiled bytecode bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` were available), so I could not directly confirm whether `swapForGas` re-checks `deadline` against `block.timestamp` on-chain. However, the fact that the "insufficient deadline" rejection message matches the Go-level check verbatim, combined with no on-chain revert reason being tested, strongly suggests deadline enforcement lives in the off-chain mempool-admission layer (`checkBalanceForSwap`), which only runs once when the transaction enters an individual node's pool.

### Impact Explanation
If deadline enforcement is not duplicated on-chain:
- A block proposer (validator) is not obligated to re-run `checkBalanceForSwap` before including a `swapForGas` transaction that has been sitting in the pool - the check is a "soft" admission-time gate for well-behaved nodes, not a chain-consensus rule.
- A `swapForGas` transaction with a low gas price can remain pending past its intended `deadline`; when eventually included (e.g., due to gas price fluctuation), the swap executes using the current AMM exchange rate/liquidity even though the user's deadline has technically expired, exactly mirroring the reported `MeritDutchAuction.mint` issue where a stale transaction executes under conditions the user never approved.
- Because the gasless flow settles by consuming the user's token balance and repaying the sponsoring proposer (`amountRepay`) from swap proceeds, a stale-deadline execution can result in the user swapping tokens at a worse rate than intended, or a mismatch between the expected and actual settlement amount used for gas repayment.

### Likelihood Explanation
Likelihood is moderate: it requires either (a) a non-conforming/adversarial proposer that includes the transaction without invoking `CheckBalance`, or (b) the honest node's tx-pool not re-validating `checkBalanceForSwap` between initial admission and eventual promotion when gas prices later become favorable. I could not verify from the available index whether `CheckBalance`/`GetCheckBalance` is re-invoked at promotion time in `blockchain/tx_pool.go` / `blockchain/tx_list.go` (both files reference `CheckBalance` but their exact call sites and timing were not retrievable in this session), which is the key uncertainty in confirming exploitability for honest-node-only scenarios.

### Recommendation
- Enforce the `deadline` check directly inside the `GaslessSwapRouter.swapForGas` on-chain function (`require(deadline >= block.timestamp)`), so that stale intents revert deterministically at execution time regardless of proposer behavior, consistent with standard swap-router patterns (e.g., Uniswap's `ensure(deadline)` modifier).
- Additionally confirm whether `kaiax/gasless`'s `CheckBalance` is re-validated immediately before block assembly/promotion, not solely at initial tx-pool admission.

### Proof of Concept
Not independently reproducible from the indexed code alone since the on-chain `GaslessSwapRouter.sol` source was not available for direct inspection; the analysis is based on: (1) the deadline check existing only as a Go function (`checkBalanceForSwap`) gating tx-pool admission, and (2) the integration test asserting the exact Go error string rather than an on-chain revert reason for expired deadlines, as shown in the cited code.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** tests/gasless_test.go (L260-262)
```go
	// reject swapTx when deadline is in the past
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, minAmountOut, amountRepaySwap, common.Big1)
	assert.ErrorContains(t, err, "insufficient deadline: deadline=1")
```
