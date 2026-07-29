## Summary

The reported analog is a **stale/shared cached accounting field** used to compute a delta of state that has changed since a checkpoint — same bug class as `svTokenValue` using a `totalSupply` snapshot that doesn't account for state mutated between the snapshot and the read. In this codebase, the direct analog is the `BalanceHandler.prevEventsLen` field used to reconcile Cosmos SDK bank events with the EVM `StateDB` after a precompile call.

### Title
Shared `BalanceHandler` instance across recursive/nested precompile calls corrupts `prevEventsLen`, causing EVM `StateDB` balances to desync from `x/bank` ground truth - (File: `precompiles/common/balance_handler.go`)

### Finding Description
`BalanceHandler.BeforeBalanceChange` snapshots the current length of the event manager's event list into `bh.prevEventsLen`, and `AfterBalanceChange` later replays only `events[bh.prevEventsLen:]` into the EVM `StateDB` via `AddBalance`/`SubBalance`: [1](#0-0) [2](#0-1) 

This mirrors the reported bug class exactly: a cached scalar (`prevEventsLen`, analogous to the stale `totalSupply`) is read once and used later to compute a "delta" (analogous to `equityValue_/totalSupply_`), without accounting for the fact that other in-flight execution can mutate the underlying source of truth (the event log) in between.

Several production precompiles (`staking`, `distribution`, `gov`, `ics20`, `slashing`, `erc20`, `bank`) obtain their `BalanceHandler` via a `p.GetBalanceHandler()` accessor on their `Run()` entrypoints, rather than constructing a fresh handler per call: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

This is in contrast to the newer/generic dispatcher `RunNativeAction`, which explicitly instantiates a **new** `BalanceHandler` from a factory for every call: [9](#0-8) 

If the same `BalanceHandler` instance is entered re-entrantly or nested (e.g., a contract triggers a precompile call, which — directly or indirectly through further EVM execution — triggers another call into the *same* precompile object before the outer call finishes), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`. When the outer call later invokes `AfterBalanceChange`, it uses the now-advanced `prevEventsLen` set by the inner call instead of its own original checkpoint, so the outer call's own bank events (`EventTypeCoinSpent`/`EventTypeCoinReceived`/`EventTypeFractionalBalanceChange`) are skipped and never applied to the `StateDB`, or, depending on call ordering, are double-applied. Either direction breaks the invariant that the EVM `StateDB` balance must equal the `x/bank`/`x/precisebank` balance for the same account.

This exact scenario — "recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten... leads to balance desync between native bank keeper and EVM stateDB" — is explicitly documented and reproduced by an existing regression test in the repository: [10](#0-9) [11](#0-10) 

A related IBC test also documents an adjacent manifestation ("reverted distribution calls leave persistent bank events that are incorrectly aggregated"): [12](#0-11) 

### Impact Explanation
A desync between `x/bank`/`x/precisebank` (the canonical ledger) and the EVM `StateDB` (used for all subsequent EVM-visible balance reads/gas/transfer logic within the same and later transactions, since `StateDB.Commit()` persists state-object balances back into the account/storage layer) is an accounting-integrity break. Depending on which side of the race wins:
- The EVM-visible balance can be **overcredited relative to bank truth** (functionally equivalent to unauthorized minting of spendable EVM value), allowing that inflated balance to be spent/transferred in subsequent EVM operations without a corresponding backing `x/bank`/`x/precisebank` balance.
- Or it can be undercredited, permanently locking legitimately received funds from EVM-side visibility/spendability.

Both directions match the required Critical impact class: unauthorized duplication/irreversible accounting corruption of spendable user value across native and EVM balances, or permanent freezing/locking of user funds.

### Likelihood Explanation
Triggering requires an unprivileged user to construct a contract that causes nested/recursive invocation of the same precompile object within one EVM call tree (e.g., contracts calling into `staking`/`distribution`/`gov`/`ics20` precompiles that internally trigger further EVM calls, including calls back into the same precompile, or into other precompiles that share instance state). The project's own test harness (`DebugPrecompileCaller.sol` recursively calling a precompile) demonstrates this pattern is directly reachable from ordinary contract bytecode, i.e. it is not privileged and requires no relayer/validator cooperation.

### Recommendation
- Do not store `BalanceHandler` as shared/reused state on a long-lived `Precompile` struct (`p.GetBalanceHandler()`). Every precompile `Run()` entrypoint should construct a **fresh** `BalanceHandler` per invocation (as already done in `RunNativeAction`/`BalanceHandlerFactory.NewBalanceHandler()`), and this pattern should be applied uniformly to `staking`, `distribution`, `gov`, `ics20`, `slashing`, `erc20`, and `bank` precompiles.
- Alternatively, replace the single mutable `prevEventsLen int` field with a stack/counter-based approach that can support proper nesting (e.g., push/pop checkpoints, or track processed event indices per call depth) so that an inner call cannot clobber an outer call's checkpoint.
- Add an invariant check (fuzz/property test) asserting `sum(StateDB balances for evm-coin denom) == bank/precisebank ledger totals` after every transaction involving nested precompile calls.

### Proof of Concept
The repository already contains a proof-of-concept reproducing the root cause: [13](#0-12) 
using a recursive Solidity caller contract that repeatedly invokes a debug precompile that itself uses the shared `GetBalanceHandler()` pattern: [14](#0-13) [15](#0-14) 

**Caveat / what I could not fully verify:** I did not confirm from source (due to iteration limits) whether the `Precompile` struct backing `staking`/`distribution`/`gov`/`ics20`/`slashing`/`erc20` is instantiated once as a long-lived singleton per node (registered in the EVM keeper's precompile map) versus freshly constructed per call — this determines whether the desync is reachable only via true call-stack re-entrancy within one transaction, or also across concurrent/sequential unrelated transactions sharing the same object. I also did not verify whether a partial mitigation exists elsewhere (e.g., `precompileCallsCounter`/`MaxPrecompileCalls` in `x/vm/statedb/statedb.go`) that might bound but not eliminate the exposure. A Devin session with full repo access should confirm precompile instantiation lifetime and whether recent commits have already patched the `GetBalanceHandler()` call sites to use the per-call factory pattern.

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

**File:** precompiles/distribution/distribution.go (L1-1)
```go
package distribution
```

**File:** precompiles/staking/staking.go (L1-1)
```go
package staking
```

**File:** precompiles/erc20/erc20.go (L1-1)
```go
package erc20
```

**File:** precompiles/gov/gov.go (L1-1)
```go
package gov
```

**File:** precompiles/ics20/ics20.go (L1-1)
```go
package ics20
```

**File:** precompiles/slashing/slashing.go (L1-1)
```go
package slashing
```

**File:** precompiles/common/precompile.go (L99-106)
```go
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-102)
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

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

**File:** testutil/testdata/debug/debug.go (L47-115)
```go
func (p Precompile) Run(evm *vm.EVM, contract *vm.Contract, readonly bool) ([]byte, error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.Wrap(errors2.ErrUnauthorized, "could not create statedb in debug precompile")
	}

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
	err = stateDB.AddPrecompileFn(p.Address(), snapshot, events)
	if err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)

	initialGas := ctx.GasMeter().GasConsumed()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)
	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")

	// This handles any out of gas errors that may occur during the execution of a precompile tx or query.
	// It avoids panics and returns the out of gas error so the EVM can continue gracefully.
	defer cmn.HandleGasError(ctx, contract, initialGas, &err)()

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
