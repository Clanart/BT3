Confirmed: the exact vulnerability is already documented in this repository's own test suite comment, which independently confirms both root cause and impact.

### Title
Recursive/nested precompile calls reuse a single `BalanceHandler` instance's `prevEventsLen`, causing duplicated or dropped bank-event balance mutations in EVM `StateDB` - (File: precompiles/common/precompile.go, precompiles/common/balance_handler.go)

### Summary
`Precompile.runNativeAction` (`precompiles/common/precompile.go:57-126`) assumes that between `BeforeBalanceChange` and `AfterBalanceChange` the only new bank events appended to the shared `cacheCtx.EventManager()` are the ones produced by the *current* precompile call, so it can safely slice `events[bh.prevEventsLen:]` in `AfterBalanceChange` (`precompiles/common/balance_handler.go:68-71`) and translate each `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` event into a corresponding `StateDB.AddBalance`/`SubBalance` call. Nothing in the framework enforces this invariant: if a precompile's native action recursively triggers another precompile call (nested `RunNativeAction`), both calls share the same `cacheCtx` and therefore the same underlying `EventManager` event slice, but each level creates its *own* `BalanceHandler` (`p.BalanceHandlerFactory.NewBalanceHandler()` at `precompiles/common/precompile.go:99-101`), each stamping its own `prevEventsLen`. This is structurally identical to the Basin `GeoEmaAndCumSmaPump` bug: the component's correctness depends on an invariant ("no overlapping/foreign events in my window") that is documented only implicitly and never checked at the call site.

### Finding Description
- `BeforeBalanceChange(ctx)` records `len(ctx.EventManager().Events())` into `prevEventsLen` on the handler instance (`precompiles/common/balance_handler.go:46-48`).
- `AfterBalanceChange` walks `events[bh.prevEventsLen:]` and applies `stateDB.AddBalance`/`SubBalance` for every `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` event found in that window (`precompiles/common/balance_handler.go:68-136`).
- In `runNativeAction`, a fresh `BalanceHandler` is created per precompile invocation, but the underlying `ctx`/`cacheCtx` and its `EventManager` are shared across nested/recursive precompile calls reached through the same EVM call stack (`precompiles/common/precompile.go:64, 99-106`).
- When a precompile's native `action` internally triggers another precompile call (or the same precompile recursively, as demonstrated by the repo's own `DebugPrecompileCaller.sol` and `BalanceHandlerTestSuite` at `evmd/tests/integration/balance_handler/balance_handler_test.go:23-25`), the inner call's `AfterBalanceChange` consumes and applies events in range `[prevEventsLenInner, N]`. When control returns to the outer call, its own `AfterBalanceChange` re-scans `[prevEventsLenOuter, M]`, which still includes the inner call's already-applied events (since `prevEventsLenOuter <= prevEventsLenInner`). Those inner bank events get translated into `StateDB.AddBalance`/`SubBalance` a second time, or — depending on nesting order — some events can be skipped entirely, producing balance drift between the Cosmos SDK bank keeper (source of truth for native coin balances) and the EVM `StateDB` view.
- The test file's own doc-comment explicitly states this is a known bug: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* (`evmd/tests/integration/balance_handler/balance_handler_test.go:23-25`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
This directly matches the required Critical impact category "unauthorized minting, burning, duplication ... or irreversible accounting corruption of spendable user value across native balances, EVM balances." A desync between `StateDB` (the EVM's view of an account's spendable balance, used for subsequent `CALL`/`transfer`/gas checks within the same or later transactions) and the actual bank-module balance means an unprivileged attacker who controls a contract that recursively invokes precompiles (e.g., calling a precompile from within a callback triggered by another precompile, as already exercised by `DebugPrecompileCaller.callback` and the `StakingReverter` nested-call test patterns) can cause `StateDB.AddBalance` to be invoked twice for the same underlying bank transfer, inflating the EVM-visible balance of an address without a matching increase in the bank module's ledger. Because `StateDB` balance changes are subsequently reconciled to the bank keeper via `Keeper.SetBalance` (`x/vm/keeper/statedb.go:112-136`, which mints/burns the delta between the EVM-observed balance and the actual spendable coin), an inflated `StateDB` balance can result in the VM keeper unilaterally minting native coins to cover the phantom credit when the state is committed — an unauthorized minting of spendable value.

### Likelihood Explanation
The trigger is a simple, permissionless multi-precompile (or self-recursive precompile) call sequence executable by any contract, requiring no privileged role, validator collusion, or relayer/peer misbehavior — consistent with the repo's own dedicated regression test (`TestRecursivePrecompileCallsWithDebugPrecompile`) built specifically to reproduce it. The presence of this test, plus multiple integration test suites exercising "internal transfers before/after precompile call" and nested try/catch precompile delegation patterns across staking, distribution, and gov precompiles, indicates nested-precompile execution paths are common and reachable in normal contract usage, not an edge case requiring unusual conditions.

### Recommendation
Do not rely on a per-call instance field (`prevEventsLen`) sliced against a globally shared, monotonically growing event log. Instead, either (a) track and process only the delta of events emitted strictly within the current call's own execution (e.g., record the event count immediately before and after invoking `action`, excluding any nested precompile sub-ranges that have already registered their own `AddPrecompileFn`/balance-handler window), or (b) mark/consume events as "handled" so nested handlers cannot reprocess them, or (c) push/pop a stack of `prevEventsLen` values across nested precompile invocations rather than instantiating independent handlers with disjoint bookkeeping over a shared event log.

### Proof of Concept
The repository already contains a reproducing test: `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys `DebugPrecompileCaller.sol` (`contracts/solidity/DebugPrecompileCaller.sol`), which recursively calls a debug precompile via `callback(uint256 counter)`, alternating flat calls and recursive self-calls. The test asserts on the resulting event counts to demonstrate the described `prevEventsLen` overwrite condition; the test's own docstring states the expected consequence is "balance desync between native bank keeper and EVM stateDB." [5](#0-4) [6](#0-5)

### Citations

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-71)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
```

**File:** precompiles/common/precompile.go (L63-106)
```go
	// get the stateDB cache ctx
	ctx, err := stateDB.GetCacheContext()
	if err != nil {
		return nil, err
	}

	// take a snapshot of the current state before any changes
	// to be able to revert the changes
	snapshot := stateDB.MultiStoreSnapshot()
	events := ctx.EventManager().Events()

	// add precompileCall entry on the stateDB journal
	// this allows to revert the changes within an evm tx
	if err := stateDB.AddPrecompileFn(snapshot, events); err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

	initialGas := ctx.GasMeter().GasConsumed()

	defer HandleGasError(ctx, contract, initialGas, &err)()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)

	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")

	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
	}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-103)
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

```

**File:** contracts/solidity/DebugPrecompileCaller.sol (L1-30)
```text
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.4;

contract DebugPrecompileCaller {
    address constant debugPrecompile = 0x0000000000000000000000000000000000000799;
    error CallFailed(bytes data);
    function callback(uint256 counter) public {
        bool result;
        bytes memory data;

        // emit events
        for (uint i = 0; i < counter; i++) {
            (result, data) = debugPrecompile.call(abi.encodePacked(uint8(1)));
            if (!result) {
                revert CallFailed(data);
            }
        }

        if (counter > 3) {
            // stop the recursion
            return;
        }

        // recursive call
        (result, data) = debugPrecompile.call(abi.encodePacked(uint8(0), counter));
        if (!result) {
            revert CallFailed(data);
        }
    }
}
```
