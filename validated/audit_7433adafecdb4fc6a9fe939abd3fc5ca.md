## Analog Found: BalanceHandler Instance-Sharing During Recursive Precompile Calls

### Title
Shared `BalanceHandler` state corruption on recursive/re-entrant precompile calls causes native-bank/StateDB balance desync - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The reentrancy report's underlying concern — state corruption from unguarded re-entrant/recursive execution paths — has a concrete analog in the precompile execution layer. The `BalanceHandler` used to reconcile native bank-module balance-change events with the EVM `StateDB` records a `prevEventsLen` marker (`BeforeBalanceChange`) and later replays events after that marker (`AfterBalanceChange`) to apply `AddBalance`/`SubBalance` to `StateDB`. [1](#0-0)  If the same `BalanceHandler` instance is reused across a recursive/nested precompile invocation (e.g., contract calls back into a precompile from within `_beforeTokenTransfer` or a `try/catch` reentry, or one precompile call invoking another), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the marker the outer call needs for its own `AfterBalanceChange` replay. [2](#0-1) 

### Finding Description
There is a repo-native test explicitly named and documented as reproducing this exact bug class:

> "BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [3](#0-2) 

The safe path, `Precompile.runNativeAction`, allocates a **fresh** `BalanceHandler` per call via `p.BalanceHandlerFactory.NewBalanceHandler()`: [4](#0-3) 

However, other precompile implementations obtain the handler via `p.GetBalanceHandler()` and drive `BeforeBalanceChange`/`AfterBalanceChange` manually (seen in the debug precompile pattern used for this exact regression test): [5](#0-4)  If `GetBalanceHandler()` returns a struct-held (shared) instance rather than a fresh one per call, then any contract that triggers a nested/recursive call into the precompile (directly, or by calling a second precompile that itself re-enters, similar to the recursive-ERC20/staking/distribution test contracts already present in the repo, e.g. `ERC20RecursiveNonRevertingPrecompileCall.sol`'s `_beforeTokenTransfer` hook calling back into `claimRewards()`) [6](#0-5)  will cause the inner call's `BeforeBalanceChange` to reset `prevEventsLen` to a later index. When the outer call's `AfterBalanceChange` runs, it will only replay events from the (now incorrect, larger) `prevEventsLen` index onward, silently dropping the outer call's own bank-event-derived balance deltas from being applied to `StateDB`. This desynchronizes the EVM-visible balance (`StateDB`) from the actual native bank-module balance — an accounting-corruption bug consistent with the "missing reentrancy guard" bug class in the seed report, but manifesting here as balance/state divergence between two ledgers rather than a Solidity-level fund drain.

### Impact Explanation
If exploitable, this breaks the "Asset-representation path" invariant (x/erc20/bank precompile 1:1 accounting must stay consistent between native coins and precompile-visible EVM balances). A `StateDB` balance that diverges from the real bank-module balance could allow an attacker to spend/withdraw funds via the EVM balance view that don't reflect actual bank holdings, or conversely cause funds to be non-deterministically frozen/lost from a user's EVM-visible balance — matching the "Critical unauthorized... or irreversible accounting corruption of spendable user value across native balances... or precompile-mediated assets" and "permanent freezing/locking of user funds" impact categories.

### Likelihood Explanation
The repository itself already contains a dedicated regression test (`balance_handler_test.go`) built specifically to reproduce "recursive precompile calls shar[ing] the same BalanceHandler instance," which strongly suggests this was a real, previously-identified defect exercised through ordinary unprivileged contract calls (deploy a contract, fund it, call it — no privileged setup needed, as shown by the test's `SendEvmTx` flow). [7](#0-6)  The extensive parallel test coverage for recursive/reentrant precompile calls elsewhere (staking `StakingReverter.sol` nested try/catch delegations, ERC20 `_beforeTokenTransfer` recursive precompile calls, ICS20 recursive precompile call tests) indicates the maintainers treat this attack surface as a known risk area that has required multiple rounds of hardening.

### Caveat / What Is Unresolved
I was **not able to fully confirm the current fixed/unfixed status** of this issue within the available tool budget:
- I could not retrieve the full source of `GetBalanceHandler()` on the `Precompile` struct to determine definitively whether it lazily creates a new instance per call or returns a shared/cached field (the grep only located 2 matches in `debug.go` and 1 in `precompile.go`, and the follow-up `read_file` calls to inspect them failed due to a tool-call parameter error before the iteration budget was exhausted).
- I could not confirm whether the `balance_handler_test.go` test currently **passes** (i.e., documents a fixed, now-safe behavior with assertion counts proving correctness) or **demonstrates a live, unresolved bug** (i.e., an accepted-but-unfixed known issue). The test only asserts specific event/debug counts, and the assertions may already validate correct behavior rather than proving a live vulnerability.
- I could not verify whether any of the "real" production precompiles (staking, distribution, bank, ERC20) actually instantiate `Precompile` with a shared `BalanceHandler` field versus always going through `RunNativeAction`'s fresh-instance path — only the test-only `debug` precompile clearly demonstrates the vulnerable pattern.

Given this uncertainty, I recommend that a Devin agent with full repository/tool access:
1. Read `precompiles/common/precompile.go` in full (specifically `GetBalanceHandler()`'s definition and the `Precompile` struct fields) to determine if `BalanceHandler` is a shared struct field vs. per-call local.
2. Check whether production precompiles (staking, distribution, bank, erc20) use the `RunNativeAction`/`BalanceHandlerFactory` fresh-instance pattern exclusively, or whether any use the `GetBalanceHandler()`-shared pattern from `debug.go`.
3. Run `evmd/tests/integration/balance_handler/balance_handler_test.go` to observe whether it currently passes (indicating the bug is fixed/mitigated) or reveals an actual desync, and inspect git history/blame on `balance_handler.go` for prior fix commits referencing this exact issue.

Because I cannot confirm this is currently reachable/unfixed with a concrete corrupted-value proof-of-concept, I present it as a strong analog candidate requiring verification rather than a fully substantiated finding.

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L45-103)
```go
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

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-142)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveNonRevertingPrecompileCall(address(this)).claimRewards() {

            } catch {

            }

        }

        super._beforeTokenTransfer(from, to, amount);
    }
```
