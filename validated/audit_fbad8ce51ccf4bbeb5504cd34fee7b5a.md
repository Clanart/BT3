### Title
Recursive precompile calls sharing one `BalanceHandler` cause `prevEventsLen` to be overwritten, silently dropping native-balance updates in the EVM StateDB - (File: precompiles/common/balance_handler.go)

### Summary
The reported liquidation-fee bug is a "reset-on-increment" class of issue: an accumulator (`liquidation_fee_usd`) is overwritten by a nested/incremental operation instead of being preserved, so previously-earned state is silently lost. The same class of bug exists in Cosmos EVM's precompile balance-reconciliation mechanism: `BalanceHandler.prevEventsLen` is a single mutable cursor that gets overwritten whenever `BeforeBalanceChange` is invoked again on the *same* handler instance. When precompile execution recurses (a precompile calling back into the EVM which calls the precompile again, or a contract composing multiple precompile calls that reuses one handler instance), the inner call's `BeforeBalanceChange` resets `prevEventsLen` to a later index, and the outer call's `AfterBalanceChange` (which runs afterward, slicing `events[bh.prevEventsLen:]`) misses events that occurred between the outer and inner "before" markers.

### Finding Description
`BalanceHandler` is the mechanism precompiles use to reconcile native `x/bank` coin-spent/coin-received (and precisebank fractional-balance) events into the EVM `StateDB` so that the StateDB balances stay consistent with bank-module balances after a precompile executes native Cosmos SDK logic (staking, distribution, gov, ICS20, ERC20 native-pair transfers, etc.): [1](#0-0) 

`BeforeBalanceChange` simply records `len(ctx.EventManager().Events())` at that instant; `AfterBalanceChange` later replays only the events emitted *after* that recorded index into `stateDB.AddBalance`/`SubBalance`: [2](#0-1) 

The generic `Precompile.runNativeAction` path in `precompiles/common/precompile.go` creates a *fresh* `BalanceHandler` for every call via `BalanceHandlerFactory.NewBalanceHandler()`: [3](#0-2) 

However, this is not universally true — some precompile implementations (e.g. the test/debug precompile, and potentially other custom `Run` implementations that don't call `RunNativeAction`) hold and reuse a single `BalanceHandler` instance across the lifetime of the precompile object rather than creating one per call: [4](#0-3) [5](#0-4) 

This exact bug is already reproduced and documented in the codebase's own test suite: [6](#0-5) 

The comment explicitly states: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* This is functionally identical to the audited bug class: an accumulator/cursor meant to represent "how much has already been accounted for" is reset by an inner/incremental operation, causing the outer operation's associated value (here, un-applied bank events representing real coin movements) to be dropped rather than combined/preserved.

### Impact Explanation
If a precompile path exists in production where recursive/nested precompile calls reuse a single `BalanceHandler` (as demonstrated for the debug precompile, and architecturally possible for any precompile implementation that stores `BalanceHandler` as a struct field instead of obtaining a fresh one per call from `RunNativeAction`), then:
- Real bank-module coin movements (from `SendCoins`, staking rewards withdrawal, ICS20 transfers, ERC20 native-pair transfers, etc.) that occur inside the outer call, but before the nested call's `BeforeBalanceChange` snapshot, will never be applied to the EVM StateDB via `AddBalance`/`SubBalance` in `AfterBalanceChange`, because they fall before the (overwritten) `prevEventsLen` cursor.
- This produces a permanent desync between `x/bank`'s actual balance and the EVM `StateDB`'s cached balance for the affected accounts. Since EVM `StateDB` balance is authoritative for subsequent EVM execution (transfers, `balanceOf` reads, further precompile calls within the same or later transactions before a fresh StateDB load), this can allow value to be duplicated (bank balance increased in real ledger, but StateDB still reflects a stale/lower value that is later re-spent) or effectively frozen/lost (StateDB balance appears lower than actual, blocking legitimate spends) — both are corruption of spendable user value across native/EVM-visible balances, matching the "Critical unauthorized ... duplication ... or irreversible accounting corruption" and "permanent freezing ... of user funds" impact classes.

### Likelihood Explanation
The core defect (single shared cursor overwritten by nested calls) is proven to exist and is exercised by an in-repo test using the debug precompile, which structurally reuses a `BalanceHandler`. Whether this pattern is reachable through the shipped, non-debug precompiles registered in production (staking, distribution, gov, ICS20, ERC20, WERC20, bech32, slashing) depends on whether any of those call chains can re-enter another (or the same) precompile while relying on a `BalanceHandler` instance that is not freshly allocated per call. All production precompile constructors shown use `cmn.NewBalanceHandlerFactory(bankKeeper)` feeding `RunNativeAction`, which does allocate a new handler per call — this appears to mitigate the issue for the standard path. Confirming whether any production precompile method internally re-enters `CallEVM`/`CallEVMWithData` in a way that triggers a second precompile invocation sharing state before the outer `AfterBalanceChange` runs would require deeper call-graph tracing than was completed here; the debug-precompile reproduction demonstrates the underlying mechanism is real and exploitable if such a reachable path exists in a shipped precompile or in future precompile additions using the same shared-handler pattern.

### Recommendation
- Make `BalanceHandler` re-entrant-safe: use a stack (or an explicit list of recorded snapshot indices) rather than a single `prevEventsLen` integer, so nested `BeforeBalanceChange`/`AfterBalanceChange` pairs push/pop rather than overwrite.
- Alternatively/additionally, enforce (with a static analysis or runtime assertion) that every precompile obtains a new `BalanceHandler` per `Run` invocation, never storing one as a shared struct field, and add an integration test that exercises recursive precompile calls across each production precompile (staking, distribution, gov, ICS20, ERC20, WERC20) to prove no desync occurs.
- Add an invariant check after every `AfterBalanceChange` comparing the aggregate bank-module delta for touched accounts against the aggregate StateDB delta, failing the transaction (not just logging) on mismatch.

### Proof of Concept
The existing repository test demonstrates the mechanism: [7](#0-6) 
It deploys a caller contract invoking the debug precompile recursively (`callback` → precompile → `CallEVMWithData` back into the caller → recursive precompile call), demonstrating that `prevEventsLen` on the shared `BalanceHandler` is overwritten across nested invocations. To confirm production-critical impact, this needs to be extended/verified against a real value-bearing precompile (e.g., staking/distribution) demonstrating actual native-balance vs. StateDB-balance divergence after a nested call, which was not completed within this investigation.

### Citations

**File:** precompiles/common/balance_handler.go (L37-48)
```go
// BalanceHandler is a struct that handles balance changes in the Cosmos SDK context.
type BalanceHandler struct {
	bankKeeper    BankKeeper
	prevEventsLen int
}

// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-88)
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
```

**File:** precompiles/common/precompile.go (L99-123)
```go
	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
	}

	bz, err = action(ctx)
	if err != nil {
		return bz, err
	}

	cost := ctx.GasMeter().GasConsumed() - initialGas

	if !contract.UseGas(cost, nil, tracing.GasChangeCallPrecompiledContract) {
		return nil, vm.ErrOutOfGas
	}

	if balanceHandler != nil {
		if err := balanceHandler.AfterBalanceChange(ctx, stateDB); err != nil {
			return nil, err
		}
	}
```

**File:** testutil/testdata/debug/debug.go (L42-46)
```go

func (p Precompile) RequiredGas(input []byte) uint64 {
	return 1000
}

```

**File:** testutil/testdata/debug/debug.go (L94-115)
```go
	res, err := p.Execute(ctx, stateDB, contract, readonly)
	if err != nil {
		return nil, err
	}

	if err != nil {
		return nil, err
	}

	cost := ctx.GasMeter().GasConsumed() - initialGas

	if !contract.UseGas(cost, nil, tracing.GasChangeCallPrecompiledContract) {
		return nil, vm.ErrOutOfGas
	}

	// Process the native balance changes after the method execution.
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}

	return res, nil
}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-106)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
	suite.Suite

	coordinator *evmibctesting.Coordinator
	chain       *evmibctesting.TestChain
}

func TestBalanceHandlerTestSuite(t *testing.T) {
	suite.Run(t, new(BalanceHandlerTestSuite))
}

func (s *BalanceHandlerTestSuite) SetupTest() {
	// Create coordinator with one chain
	s.coordinator = evmibctesting.NewCoordinator(s.T(), 1, 0, integration.SetupEvmd)
	s.chain = s.coordinator.GetChain(evmibctesting.GetEvmChainID(1))
}

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
}
```
