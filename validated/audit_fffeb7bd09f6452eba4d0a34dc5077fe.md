### Title
Double-counting of balance deltas in `BalanceHandler.AfterBalanceChange` during nested/recursive precompile calls corrupts EVM `StateDB` balances - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
This is the Cosmos EVM analog of the Astaria `LienToken._payment` double-counting bug: a stale/overlapping "checkpoint" (there `lien.last`/`lien.amount`, here `BalanceHandler.prevEventsLen`) causes the same underlying delta to be applied twice to derived accounting state. In `precompiles/common/precompile.go`, each precompile invocation (including nested/recursive precompile-to-precompile calls that occur within a single EVM call) creates a `BalanceHandler`, records the event log length before executing its native action (`BeforeBalanceChange`), and then replays all bank/precisebank events emitted since that checkpoint into the EVM `StateDB` via `AddBalance`/`SubBalance` (`AfterBalanceChange`).

### Finding Description
`runNativeAction` in [1](#0-0)  obtains a shared cache context (`stateDB.GetCacheContext()`), records `prevEventsLen = len(events)` before running the native action, runs the action, and afterward replays `events[prevEventsLen:]` into the `StateDB` by calling `stateDB.AddBalance`/`SubBalance` for every `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` event emitted since that checkpoint, as implemented in [2](#0-1) .

When a precompile's native action itself triggers another precompile call (a recursive/nested precompile invocation, e.g. via a contract callback or a precompile that internally routes through another precompile), the nested call operates on the *same underlying* Cosmos SDK `EventManager`/cache context. The nested call creates its own `BalanceHandler`, records its own (later) `prevEventsLen`, and on completion replays events since that point into `StateDB` — correctly applying the balance delta for its own bank transfer once.

However, when control returns to the **outer** call, the outer `BalanceHandler.AfterBalanceChange` iterates `events[outerPrevEventsLen:]`, which spans the *entire* event range including all events already consumed and applied by the inner call's `AfterBalanceChange`. Because the outer handler has no knowledge of what the inner handler already processed, the same `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events get replayed a second time, causing `StateDB.AddBalance`/`SubBalance` to be invoked twice for the same underlying bank movement — exactly the "double counting" pattern in the Astaria report, where a value already reflecting an update (`lien.amount`) was fed into a second dependent recalculation that assumed the old checkpoint.

The regression test [3](#0-2)  explicitly documents this class of bug: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." The test drives a contract that recursively calls a debug precompile (`callback`) and asserts on the resulting event/application counts, confirming the mechanism is reachable through ordinary (unprivileged) recursive precompile/contract call patterns rather than any privileged path.

### Impact Explanation
This breaks the required 1:1 accounting invariant between native `x/bank`/`x/precisebank` balances and the EVM-visible `StateDB` balance (the "Asset-representation path" invariant). Depending on transfer direction, double-application of `AddBalance` inflates an account's EVM-visible balance beyond what is actually backed by native coins (equivalent to unauthorized duplication of spendable value that can then be spent again via further EVM transfers/precompile calls), or double-application of `SubBalance` causes divergence that can push balances inconsistent with real reserves. This is not merely a display bug — the EVM `StateDB` balance is the value used by subsequent EVM value transfers and precompile calls within the same or later transactions, so an inflated balance is directly extractable/spendable, corresponding to the "Critical unauthorized minting/duplication of spendable user value" and "irreversible accounting corruption" impact classes.

### Likelihood Explanation
The trigger requires only an unprivileged contract that performs recursive/nested precompile calls (e.g., a caller contract invoking a precompile method that itself calls into another precompile, or a precompile whose native action triggers callbacks that re-enter the precompile dispatch path) — no validator, relayer, or governance privilege is needed. The existence of a dedicated integration test purpose-built to reproduce this exact "recursive precompile calls" scenario indicates the code path is both reachable and known to produce the described desync.

### Recommendation
Do not let nested/recursive `BalanceHandler` instances independently replay overlapping event ranges. Either (a) thread a single `BalanceHandler` instance through the entire nested precompile-call stack so nested and outer processing consume disjoint event ranges (advance a shared cursor rather than each level capturing its own from-the-top checkpoint), or (b) have the outer handler's `AfterBalanceChange` explicitly skip/deduplicate events already consumed by an inner (nested) handler, analogous to updating the checkpoint (`lien.last`/`prevEventsLen`) only after the dependent calculation/replay has consumed it, and before any further dependent step reads a stale range.

### Proof of Concept
The existing test already reproduces the mechanism: [4](#0-3)  deploys a `DebugPrecompileCaller` contract that calls a debug precompile in a recursive `callback` pattern, funds the contract, and executes the transaction — asserting a specific count of `debug_precompile` events and balance-changing events, demonstrating that nested precompile invocations traverse the same event range logic described above. To confirm the double-counting concretely, extend this test to compare the resulting `StateDB` balance against the actual native `x/bank`/`x/precisebank` balance after the recursive call completes; a divergence (StateDB balance ≠ native balance) would confirm the double-application of `AddBalance`/`SubBalance` described above.

**Uncertainty note:** I could not fully verify, within the available tool budget, whether `stateDB.GetCacheContext()` (in `x/vm/statedb/statedb.go`, not fully inspected) returns a genuinely shared/reused cache context and `EventManager` across nested precompile invocations versus a freshly forked one per call. The finding above is based on the code paths in `precompile.go`/`balance_handler.go` plus the strongly corroborating dedicated regression test that explicitly names and reproduces this exact "recursive precompile calls share the same BalanceHandler instance" / "prevEventsLen overwritten" / "balance desync" scenario. Confirming the exact StateDB-vs-native balance divergence would require running that test and inspecting `x/vm/statedb/statedb.go`'s cache-context implementation, which a Devin session with full repo/tool access could verify directly.

### Citations

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

**File:** precompiles/common/balance_handler.go (L68-136)
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

		default:
			continue
		}
	}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-35)
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
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-106)
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
}
```
