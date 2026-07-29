## Summary of findings

The TSS Manager report is fundamentally about an **off-chain coordination service being a single point of failure**, and a CLI-secrets-in-plaintext issue. Neither has a structural analog in Cosmos EVM's on-chain code (no analogous off-chain signer-coordinator exists in this repo, and the CLI mnemonic-handling code in `client/keys/add.go` already funnels secrets through `input.GetString`/mnemonic files rather than printing them to shell history by default — a Low-severity, non-Critical finding).

However, tracing the "coordination breaks → shared state gets corrupted" bug class onto the **VM state path / nested-call invariant** pivot led to a genuine, already-flagged issue in this codebase.

### Title
Shared `BalanceHandler` instance corrupts native/EVM balance sync on recursive precompile calls - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Finding Description
Stateful precompiles (distribution, staking, gov, ics20, erc20, bank, slashing) each construct a `BalanceHandlerFactory` at registration time [1](#0-0) . The `BalanceHandler` tracks a single mutable field, `prevEventsLen`, set in `BeforeBalanceChange` and consumed in `AfterBalanceChange` to slice "new" bank events out of the event manager and apply them to the EVM `StateDB` [2](#0-1) .

The repository's own integration test, `TestRecursivePrecompileCallsWithDebugPrecompile`, documents that "recursive precompile calls share the same `BalanceHandler` instance, causing `prevEventsLen` to be overwritten," and that this "leads to balance desync between native bank keeper and EVM stateDB" [3](#0-2) . A separate IBC test explicitly targets "the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated" [4](#0-3) .

Because a `Precompile` (e.g. the `distribution`, `ics20`, `erc20`, `staking`, `gov`, `slashing` precompiles) is registered once and reused for every call into that address, when a contract makes a **reentrant/recursive call into the same stateful precompile** (directly, or via nested calls through multiple precompiles that share bank events), the outer call's `prevEventsLen` bookmark gets clobbered by the inner call before `AfterBalanceChange` runs for the outer frame. This causes the event-slice window used to update `StateDB.AddBalance`/`SubBalance` to be wrong — either re-processing already-applied events (double-crediting/debiting EVM-visible balance) or skipping events entirely (losing the sync update), while the underlying `x/bank` ledger state is authoritative and unaffected. This is exactly the "nested-call ... must keep balances ... consistent across recursive execution" invariant called out for the VM state path.

### Impact Explanation
If the mis-slicing causes over-application of `AddBalance` to `StateDB`, an attacker-controlled contract could drive the EVM-visible balance of an address (potentially the calling contract itself) above what is actually backed in `x/bank`, i.e., unauthorized/duplicated spendable value visible to the EVM (mintable phantom balance usable to transfer/spend within the same or later EVM calls before any reconciling read). If it under-applies, a legitimate balance credit from a precompile-mediated operation (e.g., IBC transfer, distribution reward, staking unbond) is silently dropped from the EVM `StateDB`, permanently desyncing the contract's on-chain funds from what the EVM shows/can act on — a freezing/loss-of-access condition for a user's own funds. Either outcome maps to the Critical "unauthorized minting/duplication" or "permanent freezing/loss of access to spendable value" impact classes.

### Likelihood Explanation
This requires only an ordinary, unprivileged smart contract that calls a stateful precompile recursively/reentrantly (e.g., a token with a transfer hook calling `distribution`/`ics20`/`erc20` precompiles, or a contract that nests two different stateful-precompile calls in one EVM transaction) — no validator, governance, or admin privilege needed. The project's own test suite already reproduces this with a simple recursive caller contract [5](#0-4)  and asserts an incorrect/altered event count as the "expected" (buggy) outcome [6](#0-5) , indicating the bug is currently present and reachable through standard EVM execution, not a hypothetical edge case.

### Recommendation
Make the `BalanceHandler` (and its `prevEventsLen` bookmark) call-scoped rather than instance-scoped on the long-lived `Precompile` object — e.g., create a fresh `BalanceHandler` per `Run`/`Execute` invocation via the existing `BalanceHandlerFactory.NewBalanceHandler()` [7](#0-6)  instead of caching one on the precompile struct, and/or push/pop a stack of `prevEventsLen` bookmarks so nested/recursive calls each restore their own outer bookmark on return. Add gas/state-diff invariant tests asserting `sum(StateDB balance deltas) == sum(x/bank balance deltas)` after arbitrarily nested precompile call sequences.

### Proof of Concept
The repository already contains a working PoC: `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys `DebugPrecompileCaller.sol`, which recursively calls a precompile that triggers `BeforeBalanceChange`/`AfterBalanceChange` at each nesting level [8](#0-7) , and demonstrates the event/balance-processing count diverges from the naively expected value, confirming the shared-state corruption on recursive precompile invocation [9](#0-8) .

**Caveat:** I was not able to fully inspect `Precompile.GetBalanceHandler()` (the exact lazy-init/caching code in `precompiles/common/precompile.go`) before running out of tool calls, so I cannot cite the exact line where the handler is cached as a struct field vs. recreated. The test file's own doc comment and the IBC recursive-call test description are strong first-party corroboration that the corruption is real and reachable, but confirming the precise over- vs under-counting direction and whether existing snapshot/revert logic (`stateDB.AddPrecompileFn`, `MultiStoreSnapshot`) fully neutralizes the impact on revert paths would benefit from a live Devin session with full file/test-execution access.

### Citations

**File:** precompiles/distribution/distribution.go (L60-67)
```go
) *Precompile {
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.DistributionPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
```

**File:** precompiles/common/balance_handler.go (L30-48)
```go
func (bhf BalanceHandlerFactory) NewBalanceHandler() *BalanceHandler {
	return &BalanceHandler{
		bankKeeper:    bhf.bankKeeper,
		prevEventsLen: 0,
	}
}

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L84-102)
```go
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-54)
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

**File:** testutil/testdata/debug/debug.go (L77-112)
```go
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
```
