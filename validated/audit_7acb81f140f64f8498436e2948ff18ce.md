## Analysis

I traced the "missing events → hidden state change" bug class in this Cosmos EVM repo and found its closest analog isn't a *missing* event, but a **shared/aliased event-cursor state** in the precompile balance-sync mechanism (`precompiles/common/balance_handler.go`), which the repo's own test suite explicitly documents as *"the balance handler bug"* and reproduces with recursive precompile calls.

Everything else I checked (`x/erc20` `ConvertERC20`/`ConvertCoinNativeERC20`, `x/precisebank` mint/burn, `x/vm/statedb` journal/revert, ICS20 precompile transfer, ante-handler fee deduction/refund) has explicit invariant checks (`ErrBalanceInvariance`, remainder validation, journal-based revert, balance/fee assertions in tests) that appear to preserve the required invariants. I could not find a broken invariant there. [1](#0-0) [2](#0-1) 

### Title
Balance desync between native bank keeper and EVM StateDB via BalanceHandler event-window aliasing on recursive precompile calls - (File: precompiles/common/balance_handler.go)

### Summary
`BalanceHandler` synchronizes native `x/bank` coin movements (and `x/precisebank` fractional movements) triggered by a precompile call into the EVM `StateDB` by recording an event-log cursor (`prevEventsLen`) before the call and replaying only the events emitted after that cursor. The repository's own integration test, `TestRecursivePrecompileCallsWithDebugPrecompile`, is explicitly labeled as reproducing *"the balance handler bug where recursive precompile calls share the same `BalanceHandler` instance, causing `prevEventsLen` to be overwritten... leading to balance desync between native bank keeper and EVM stateDB."*

### Finding Description
`BalanceHandler.BeforeBalanceChange` stores `prevEventsLen = len(ctx.EventManager().Events())`, and `AfterBalanceChange` later replays `events[bh.prevEventsLen:]` to update `StateDB` balances via `AddBalance`/`SubBalance`. [3](#0-2) 

If a precompile call recursively invokes another precompile call (e.g., a contract that calls a precompile method which internally triggers another precompile-mediated bank operation, or a contract re-entering the same precompile before the outer call's `AfterBalanceChange` runs), and both calls share the *same* `BalanceHandler` instance (rather than each nested invocation getting an isolated cursor / instance), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` to a later index. When the outer call's `AfterBalanceChange` subsequently executes, it replays only `events[prevEventsLen:]` using the *inner* call's cursor — which is either too far forward (silently skipping the outer call's own bank events, so `StateDB` never reflects a balance change that already happened in `x/bank`) or the window is otherwise misaligned relative to what actually occurred for that call's scope.

This is the same underlying pattern as the seed report ("PolicyProposals/PolicyVotes/TrustedNodes fail to emit events for state changes that off-chain/downstream consumers rely on"), generalized to an event-driven state synchronization primitive: the code doesn't fail to emit the bank events themselves, but the *consumer of those events* uses a shared, overwritable cursor, so an in-scope subset of already-emitted events is silently dropped or double-processed for a given `StateDB` reconciliation pass. Because this primitive is the sole channel keeping the EVM `StateDB` account balances consistent with the authoritative `x/bank`/`x/precisebank` ledger for *every* precompile (`erc20`, `staking`, `distribution`, `gov`, `slashing`, `ics20`), a discrepancy here breaks the fundamental 1:1 accounting invariant between native coins and EVM-visible balances (Smart Audit Pivot: Asset-representation path). [4](#0-3) [5](#0-4) 

### Impact Explanation
If the `StateDB` balance for an account diverges from the true `x/bank` balance (in either direction), this is an irreversible accounting corruption of spendable user value: subsequent EVM-visible balance reads (`eth_getBalance`, contract `.balance`, ERC20 wrapper views) would be inconsistent with actual spendable funds, and further EVM transfers computed against the wrong `StateDB` balance could either strand real funds (under-crediting) or allow spending value that doesn't exist in the ledger (over-crediting) once committed via `StateDB.Commit()` (`x/vm/statedb/statedb.go` `commitWithCtx`, which persists `stateObjects` balances directly to the account keeper without reconciling against `x/bank`). This matches the "Critical unauthorized minting/duplication/irreversible accounting corruption" and "Critical permanent freezing/theft" impact classes.

### Likelihood Explanation
**I could not confirm this is currently exploitable end-to-end** — the existing test (`TestRecursivePrecompileCallsWithDebugPrecompile`) passes and only asserts event counts (15 total events, 10 `debug_precompile` events), not that account balances between `x/bank` and `StateDB` actually diverge. It's possible the current code already mitigates the described bug (e.g., precompiles may create a fresh `BalanceHandler` per top-level call rather than truly sharing one across nested/recursive invocations, or the mitigation only covers the specific recursion pattern tested). I was not able to read `precompiles/common/precompile.go`'s `RunNativeAction` (where `BalanceHandlerFactory.NewBalanceHandler()` is actually invoked and where the reentrancy/nesting boundary is enforced) within the available iterations, so I cannot state with certainty whether an unprivileged user can currently trigger the described desync via a contract that performs nested precompile calls (e.g., staking→bank precompile chains, or ERC20-precompile calling back into itself through a malicious token hook). This is a genuine gap in my verification.

### Recommendation
Have a Devin agent with full repo access:
1. Read `precompiles/common/precompile.go`'s `RunNativeAction` to determine exactly when/how often `BalanceHandlerFactory.NewBalanceHandler()` is called relative to nested/recursive precompile invocations, and whether `precompileCallsCounter`/`MaxPrecompileCalls` or snapshot/revert logic already isolates each call's `prevEventsLen`.
2. Extend `evmd/tests/integration/balance_handler/balance_handler_test.go` (or add a new test) to explicitly assert `x/bank` `GetBalance` equals `StateDB.GetBalance` (converted) after nested/recursive precompile calls across at least two real precompiles that both move bank balances (e.g., `staking` delegate + `distribution` withdraw nested via a caller contract), not just event counts.
3. If a divergence is found, ensure `BalanceHandler` state (`prevEventsLen`) is scoped per call-frame (e.g., pushed/popped on a stack, or a fresh handler instance created and consumed strictly within each `RunNativeAction` invocation without being shared across re-entrant calls).

### Proof of Concept
Not independently reproduced — the repository's own `evmd/tests/integration/balance_handler/balance_handler_test.go` (`TestRecursivePrecompileCallsWithDebugPrecompile`) is the closest existing reproduction harness for triggering recursive precompile calls with a shared `BalanceHandler`, but as currently written it does not assert the balance-divergence outcome, so I cannot confirm from static analysis alone whether it still produces incorrect `StateDB` balances in the current codebase state.

### Citations

**File:** precompiles/common/balance_handler.go (L30-68)
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

// AfterBalanceChange processes the recorded events and updates the stateDB accordingly.
// It handles the bank events for coin spent and coin received, updating the balances
// of the spender and receiver addresses respectively.
//
// NOTES: Balance change events involving BlockedAddresses are bypassed.
// Native balances are handled separately to prevent cases where a bank coin transfer
// initiated by a precompile is unintentionally overwritten by balance changes from within a contract.

// Typically, accounts registered as BlockedAddresses in app.go—such as module accounts—are not expected to receive coins.
// However, in modules like precisebank, it is common to borrow and repay integer balances
// from the module account to support fractional balance handling.
//
// As a result, even if a module account is marked as a BlockedAddress, a keeper-level SendCoins operation
// can emit an x/bank event in which the module account appears as a spender or receiver.
// If such events are parsed and used to invoke StateDB.AddBalance or StateDB.SubBalance, authorization errors can occur.
//
// To prevent this, balance changes from events involving blocked addresses are not applied to the StateDB.
// Instead, the state changes resulting from the precompile call are applied directly via the MultiStore.
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-41)
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
```

**File:** precompiles/erc20/erc20.go (L79-98)
```go
func NewPrecompile(
	tokenPair erc20types.TokenPair,
	bankKeeper cmn.BankKeeper,
	erc20Keeper Erc20Keeper,
	transferKeeper ibcutils.TransferKeeper,
) *Precompile {
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.GasConfig{},
			TransientKVGasConfig:  storetypes.GasConfig{},
			ContractAddress:       tokenPair.GetERC20Contract(),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
		ABI:            ABI,
		tokenPair:      tokenPair,
		BankKeeper:     bankKeeper,
		erc20Keeper:    erc20Keeper,
		transferKeeper: transferKeeper,
	}
}
```

**File:** precompiles/staking/staking.go (L53-73)
```go
func NewPrecompile(
	stakingKeeper cmn.StakingKeeper,
	stakingMsgServer stakingtypes.MsgServer,
	stakingQuerier stakingtypes.QueryServer,
	bankKeeper cmn.BankKeeper,
	addrCdc address.Codec,
) *Precompile {
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.StakingPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
		ABI:              ABI,
		stakingKeeper:    stakingKeeper,
		stakingMsgServer: stakingMsgServer,
		stakingQuerier:   stakingQuerier,
		addrCdc:          addrCdc,
	}
}
```
