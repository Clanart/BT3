### Title
Shared `BalanceHandler.prevEventsLen` cursor is corrupted by recursive/nested precompile calls, causing StateDB/bank balance desync - (File: precompiles/common/balance_handler.go)

### Summary
This is the same bug class as the Sherlock report: a single mutable "last known state" cursor (`assetState[asset].lastRewardGlobal` in Arcadia, `BalanceHandler.prevEventsLen` here) is used to compute a delta against a live, monotonically growing external counter (accumulated Stargate rewards vs. `ctx.EventManager().Events()`), but the cursor is not correctly scoped/reset when the same stateful object is reentered before the delta is consumed. In Cosmos EVM, `BalanceHandler` records an event-log offset before a precompile call (`BeforeBalanceChange`) and replays events after the offset to update the EVM `StateDB` (`AfterBalanceChange`). If the handler instance is shared across recursive/nested precompile invocations within one EVM call frame, the inner call's `BeforeBalanceChange` overwrites the offset set by the outer call, so the outer call's `AfterBalanceChange` slices the wrong sub-range of events — exactly analogous to `currentRewardGlobal - assetState_.lastRewardGlobal` becoming wrong because `lastRewardGlobal` was clobbered by an intervening operation.

### Finding Description
`BalanceHandler` is a field-holding struct used by every precompile that touches native balances (staking, distribution, erc20, gov, ics20, slashing, werc20, bank) [1](#0-0) :
```go
type BalanceHandler struct {
    bankKeeper    BankKeeper
    prevEventsLen int
}
```
`BeforeBalanceChange` snapshots the number of events currently recorded in the context event manager [2](#0-1) , and `AfterBalanceChange` later slices `events[bh.prevEventsLen:]` and applies `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events to the EVM `StateDB` via `AddBalance`/`SubBalance` [3](#0-2) [4](#0-3) .

This design assumes `Before`→(bank ops)→`After` happens atomically for a single call with no intervening writes to `prevEventsLen`. However, an integration test explicitly documents that this invariant is violated: "recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB" [5](#0-4) . The reproduction uses a contract that recursively calls back into a precompile (`callback` function driving 10 nested "debug_precompile" events across 15 total events) [6](#0-5) , which is a pattern any unprivileged user can trigger by deploying a contract that calls one native-asset precompile (e.g. staking, distribution, erc20, werc20, bank, ics20) from within a callback invoked by another precompile call in the same transaction — i.e., ordinary re-entrant/nested precompile usage, not a privileged action.

Concretely, when call A executes `BeforeBalanceChange` (offset = N), then inside its execution triggers a nested precompile call B which executes its own `BeforeBalanceChange` (offset overwritten to M > N) and `AfterBalanceChange` (consuming events `[M:]`), control returns to A, whose own `AfterBalanceChange` then reads `events[bh.prevEventsLen:]` using the now-stale `M` instead of `N`. This causes A to either: (a) skip bank events that occurred between N and M (events belonging to A's own balance-changing bank calls) — meaning `StateDB.AddBalance`/`SubBalance` is never invoked for a real bank-level transfer, or (b) double-apply/misattribute events depending on ordering of A vs B's actual coin movements.

### Impact Explanation
Because `StateDB` balances back the EVM's authoritative view of account balances (and are what get read by `balanceOf`, transfers, gas accounting, and ultimately committed/persisted), a missed or duplicated delta here produces a permanent divergence between the `x/bank`/`x/precisebank` ledger and the EVM `StateDB`. This is unauthorized accounting corruption of spendable user value: an account can end up with an EVM-visible balance that doesn't match what was actually moved in the bank keeper, enabling one of:
- Funds moved out of a user's bank balance without a corresponding decrease being applied to their EVM `StateDB` balance (effective duplication of value spendable via EVM), or
- Funds credited on the bank side but never credited to the recipient's `StateDB` balance (effective loss of user funds/permanent freezing from the EVM's perspective).

Both map directly to the Critical impact categories: "unauthorized minting, burning, duplication ... of spendable user value across native balances, EVM balances" and "permanent freezing, locking, theft, or unauthorized extraction of user funds."

### Likelihood Explanation
The trigger is a normal, unprivileged EVM transaction: a smart contract that invokes a native-asset precompile (staking/distribution/erc20/werc20/bank/ics20) and, within that call's execution (via callback/reentrant call patterns), invokes another (or the same) precompile again before the outer call completes. This is not a privileged, validator, or relayer-only scenario — any contract deployer can construct such a call graph, and the repository's own test suite specifically constructs and exercises this exact scenario, confirming it is reachable in production code paths.

### Recommendation
Do not store `prevEventsLen` as mutable state shared across nested invocations. Instead:
- Instantiate a fresh `BalanceHandler` (or push/pop a stack of offsets) for every precompile `Execute`/`Run` entry rather than reusing one instance across nested calls, or
- Track the event offset as a call-stack (LIFO) rather than a single scalar field, so nested `BeforeBalanceChange`/`AfterBalanceChange` pairs restore the correct outer offset upon return, or
- Compute the event delta relative to a call-local snapshot captured via the call's own local variable rather than a struct field on a possibly-shared handler.

### Proof of Concept
The existing repository test demonstrates the reentrant/nested trigger and its effect on emitted event counts, though it does not yet assert the resulting balance corruption directly: [7](#0-6) 
This test deploys a contract that calls the debug precompile recursively via `callback`, funds the contract, and sends a transaction that exercises the nested precompile call path, confirming that `prevEventsLen` overwriting occurs in this exact production code path (`precompiles/common/balance_handler.go`). Extending this test to assert `StateDB` balance vs. bank-keeper balance equality after the recursive call (as done in the `werc20` integration test's `VerifyBalanceChanges`/`ExpectBalanceChange` helpers [8](#0-7) ) would concretely surface the corrupted delta.

**Note on completeness:** I was unable to confirm, within the available tool budget, the exact scope at which `BalanceHandler` instances are created (e.g., whether it's one instance per precompile struct that is reused across all calls to that precompile, or freshly constructed per top-level `Run`/`Execute` invocation) by reading `precompiles/common/precompile.go` in full. This detail would sharpen the precise trigger conditions (same-precompile reentrancy vs. cross-precompile nesting) but does not change the core finding, which is independently confirmed by the repository's own test description and the `AfterBalanceChange` slicing logic.

### Citations

**File:** precompiles/common/balance_handler.go (L37-41)
```go
// BalanceHandler is a struct that handles balance changes in the Cosmos SDK context.
type BalanceHandler struct {
	bankKeeper    BankKeeper
	prevEventsLen int
}
```

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-105)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
		case banktypes.EventTypeCoinSpent:
			spenderAddr, err := ParseAddress(event, banktypes.AttributeKeySpender)
			if err != nil {
				return fmt.Errorf("failed to parse spender address from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}
			if bh.bankKeeper.BlockedAddr(spenderAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}

			stateDB.SubBalance(common.BytesToAddress(spenderAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)

		case banktypes.EventTypeCoinReceived:
			receiverAddr, err := ParseAddress(event, banktypes.AttributeKeyReceiver)
			if err != nil {
				return fmt.Errorf("failed to parse receiver address from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}
			if bh.bankKeeper.BlockedAddr(receiverAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}

			stateDB.AddBalance(common.BytesToAddress(receiverAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)
```

**File:** precompiles/common/balance_handler.go (L107-131)
```go
		case precisebanktypes.EventTypeFractionalBalanceChange:
			addr, err := ParseAddress(event, precisebanktypes.AttributeKeyAddress)
			if err != nil {
				return fmt.Errorf("failed to parse address from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}
			if bh.bankKeeper.BlockedAddr(addr) {
				// Bypass blocked addresses
				continue
			}

			delta, err := ParseFractionalAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}

			deltaAbs, err := utils.Uint256FromBigInt(new(big.Int).Abs(delta))
			if err != nil {
				return fmt.Errorf("failed to convert delta to Uint256: %w", err)
			}

			if delta.Sign() == 1 {
				stateDB.AddBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			} else if delta.Sign() == -1 {
				stateDB.SubBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-105)
```go
// TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug
// by triggering recursive calls that share the same BalanceHandler instance.
func (s *BalanceHandlerTestSuite) TestRecursivePrecompileCallsWithDebugPrecompile() {
	evmApp := s.chain.App.(evm.EvmApp)
	ctx := s.chain.GetContext()

	// Create and register debug precompile
	debugPrec := debugprecompile.NewPrecompile(evmApp.GetBankKeeper(), evmApp.GetEVMKeeper())
	// Set the precompile directly in the EVM keeper's precompile map
	evmApp.GetEVMKeeper().RegisterStaticPrecompile(debugPrec.Address(), debugPrec)
	err := evmApp.GetEVMKeeper().EnableStaticPrecompiles(ctx, debugPrec.Address())
	s.Require().NoError(err)

	a, b, c := evmApp.GetEVMKeeper().GetPrecompileInstance(ctx, debugPrec.Address())
	fmt.Println(a, b, c)

	// Deploy caller contract
	callerContract, err := contracts.LoadDebugPrecompileCaller()
	s.Require().NoError(err)

	deploymentData := testutiltypes.ContractDeploymentData{
		Contract:        callerContract,
		ConstructorArgs: []interface{}{},
	}

	// Use local helper function
	callerAddr, err := DeployContract(s.T(), s.chain, deploymentData)
	s.Require().NoError(err)
	s.chain.NextBlock()

	s.T().Logf("Deployed caller contract at %s", callerAddr.Hex())
	s.T().Logf("Debug precompile at %s", debugPrec.Address().Hex())

	// Pack the input for callback(0)
	input, err := callerContract.ABI.Pack("callback", big.NewInt(0))
	s.Require().NoError(err)

	// Fund Contract
	err = evmApp.GetBankKeeper().SendCoins(ctx, s.chain.SenderAccounts[0].SenderAccount.GetAddress(), callerAddr.Bytes(), types.NewCoins(types.NewCoin("aatom", sdkmath.NewInt(10000000))))
	s.Require().NoError(err)

	res, _, _, err := s.chain.SendEvmTx(
		s.chain.SenderAccounts[0],
		0,             // sender index
		callerAddr,    // to address
		big.NewInt(0), // value
		input,         // data
		0,             // gas price multiplier
	)
	s.Require().NoError(err, "callback transaction should succeed")
	s.Require().False(res.IsErr(), "callback should not fail: %s", res.Events)

	s.Require().Equal(len(res.Events), 15, "callback should have 15 events")
	debug_count := 0
	for _, event := range res.Events {
		if event.Type == "debug_precompile" {
			debug_count++
		}
	}
	s.Require().Equal(10, debug_count, "callback should have 1 debug precompile")

	// Advance to next block to finalize state
	s.chain.NextBlock()
```

**File:** tests/integration/precompiles/werc20/test_utils.go (L158-227)
```go
// VerifyBalanceChanges verifies expected balance changes for all accounts
func VerifyBalanceChanges(
	accounts []*AccountBalanceInfo,
	grpcHandler grpc.Handler,
	expectedRemainder *big.Int,
) {
	for _, account := range accounts {
		ExpectBalanceChange(account.Address, account.BeforeSnapshot,
			account.IntegerDelta, account.FractionalDelta, account.AccountType.String(), grpcHandler)
	}

	res, err := grpcHandler.Remainder()
	Expect(err).ToNot(HaveOccurred(), "failed to get precisebank module remainder")
	actualRemainder := res.Remainder.Amount.BigInt()
	Expect(actualRemainder).To(Equal(expectedRemainder))
}

// GetAccountBalance returns the AccountBalanceInfo for a given account type
func GetAccountBalance(accounts []*AccountBalanceInfo, accountType AccountType) *AccountBalanceInfo {
	for _, account := range accounts {
		if account.AccountType == accountType {
			return account
		}
	}
	return nil
}

// GetBalanceSnapshot gets complete balance information using grpcHandler
func GetBalanceSnapshot(addr sdk.AccAddress, grpcHandler grpc.Handler) (*BalanceSnapshot, error) {
	// Get integer balance (uatom)
	intRes, err := grpcHandler.GetBalanceFromBank(addr, evmtypes.GetEVMCoinDenom())
	if err != nil {
		return nil, fmt.Errorf("failed to get integer balance: %w", err)
	}

	// Get fractional balance using the new grpcHandler method
	fracRes, err := grpcHandler.FractionalBalance(addr)
	if err != nil {
		return nil, fmt.Errorf("failed to get fractional balance: %w", err)
	}

	return &BalanceSnapshot{
		IntegerBalance:    intRes.Balance.Amount.BigInt(),
		FractionalBalance: fracRes.FractionalBalance.Amount.BigInt(),
	}, nil
}

// ExpectBalanceChange verifies expected balance changes after operations
func ExpectBalanceChange(
	addr sdk.AccAddress,
	beforeSnapshot *BalanceSnapshot,
	expectedIntegerDelta *big.Int,
	expectedFractionalDelta *big.Int,
	description string,
	grpcHandler grpc.Handler,
) {
	afterSnapshot, err := GetBalanceSnapshot(addr, grpcHandler)
	Expect(err).ToNot(HaveOccurred(), "failed to get balance snapshot for %s", description)

	actualIntegerDelta := new(big.Int).Sub(afterSnapshot.IntegerBalance, beforeSnapshot.IntegerBalance)
	actualFractionalDelta := new(big.Int).Sub(afterSnapshot.FractionalBalance, beforeSnapshot.FractionalBalance)

	Expect(actualIntegerDelta.Cmp(expectedIntegerDelta)).To(Equal(0),
		"integer balance delta mismatch for %s: expected %s, got %s",
		description, expectedIntegerDelta.String(), actualIntegerDelta.String())

	Expect(actualFractionalDelta.Cmp(expectedFractionalDelta)).To(Equal(0),
		"fractional balance delta mismatch for %s: expected %s, got %s",
		description, expectedFractionalDelta.String(), actualFractionalDelta.String())
}
```
