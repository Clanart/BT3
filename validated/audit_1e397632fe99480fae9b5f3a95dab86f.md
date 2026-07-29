### Title
Native token value sent alongside calls to non-WERC20/ERC20 precompiles (staking, distribution, gov, slashing, bank, ICS20, etc.) is permanently locked at the precompile address - (File: `precompiles/common/precompile.go`, `precompiles/staking/staking.go`, `precompiles/distribution/distribution.go`, `precompiles/slashing/slashing.go`, `precompiles/bank/bank.go`, `precompiles/ics20/ics20.go`)

### Summary
The external report describes ETH becoming permanently locked in `payable` functions that have no path to recover the value (Multicall/PanopticHelper). The `x/vm` precompile framework has a structurally identical bug class: only the `erc20` precompile explicitly rejects `contract.Value() > 0` [1](#0-0) , and only the `werc20` precompile actively returns received value to the caller via the bank keeper [2](#0-1) . Every other stateful precompile (`staking`, `distribution`, `slashing`, `gov`, `bank`, `ics20`, etc.) never inspects `contract.Value()` in its `Execute`/`Run` path, so any native token value attached to a low-level call into these precompiles is transferred into the precompile's EVM balance by the EVM's normal value-transfer semantics, executed as a normal method call, and never returned or accounted for.

### Finding Description
The common precompile base (`RunNativeAction` / `runNativeAction`) only manages the SDK cache-context snapshot, gas metering, and the optional `BalanceHandler` before/after hooks — it never checks or redistributes `contract.Value()` [3](#0-2) .

Compare the two precompiles that do address this:
- `erc20` precompile explicitly reverts any call carrying value, with a comment stating the exact concern from the external report — that funds sent to it cannot be recovered: [1](#0-0) 
- `werc20` precompile explicitly forwards `contract.Value()` back to the caller via `BankKeeper.SendCoins` in its `Deposit` handler [2](#0-1) 

All other precompiles' `Execute` functions dispatch directly to their handlers without any such guard:
- `staking.Execute` [4](#0-3) 
- `distribution.Execute` [5](#0-4) 
- `slashing.Execute` [6](#0-5) 
- `bank.Execute` [7](#0-6) 
- `ics20.Execute` [8](#0-7) 

In the go-ethereum EVM call path, `Context.Transfer` moves `value` from caller to callee **before** the callee's code (or precompile `Run`) executes, regardless of whether the callee is a precompile. This happens purely at the EVM/StateDB level and is independent of the Solidity-level `payable` annotations in the precompile's own interface (e.g. `StakingI.sol`). Consequently, an attacker or naive integrator can bypass any "non-payable" restriction implied by the Solidity interfaces (which are just ABI metadata, not enforced by the precompile dispatcher) by directly issuing a low-level call such as `stakingPrecompileAddr.call{value: X}(abi.encodeWithSelector(delegateSelector, ...))`. Because none of these precompiles check or reject `contract.Value()`, the call succeeds, `X` native tokens are credited to the precompile's EVM balance in the StateDB, and the underlying Cosmos SDK `x/bank`/`x/staking`/etc. logic proceeds unaware of the transferred value (since these methods take an explicit `amount` argument rather than `msg.value`).

The precompile address (e.g. `0x...800` staking, `0x...801` distribution, `0x...806` ICS20, `0x...804` bank, `0x...805`/similar slashing) is not an EOA and has no "withdraw own balance" entrypoint the way `werc20.Deposit` does. There is no code path, governance action, or module keeper hook shown in the codebase that later sweeps or reconciles a precompile's own EVM-side native balance back into circulation. The value becomes permanently stranded native-token balance at a precompile address, unreachable by both the original sender and the protocol.

### Impact Explanation
This is a broken-invariant class the audit scope explicitly calls Critical: "permanent freezing, locking, theft, or unauthorized extraction of user funds... across native balances, EVM balances." Every unprivileged EOA or contract that constructs a raw low-level call with `value > 0` targeting `staking`, `distribution`, `slashing`, `bank`, `ics20`, or any other precompile lacking the `erc20`-style guard can cause user (or their own) funds to become permanently locked at that precompile's address, with no protocol-level recovery mechanism identified in the codebase. Because the precisebank/erc20/vm balance accounting treats the precompile address like any other account, its "locked" balance also silently inflates the apparent EVM-side balance sheet at that fixed system address, diverging from the actual spendable-value invariant the protocol otherwise tries hard to maintain (as evidenced by the special-cased guards in `erc20` and `werc20`).

### Likelihood Explanation
High from a triggering standpoint — no privileged role, relayer, or validator collusion is required. Any user or contract can issue a raw `call{value: X}(...)` to a well-known precompile address. The Solidity interfaces (`StakingI.sol`, `ICS20I.sol`, etc.) declaring functions as non-payable does not stop this at the EVM level, since the payable check is enforced only by Solidity-compiler-generated dispatcher code in normal contracts, not by the precompile's manual `Execute` switch statements. The project's own code acknowledges awareness of exactly this issue by adding the guard only to `erc20` and the forwarding logic only to `werc20`, but omitting equivalent protection everywhere else.

### Recommendation
Add the same `contract.Value().Sign() == 1` rejection (or forward-to-caller logic mirroring `werc20.Deposit`) to the `Execute`/`Run` path of every precompile that does not intentionally consume `msg.value` as part of its economic logic: `staking`, `distribution`, `slashing`, `gov`, `bank`, `ics20`, `evidence`, `callbacks`, `bech32`, and any others sharing the `cmn.Precompile` base without a value check. This should ideally be centralized in `precompiles/common/precompile.go`'s `runNativeAction` so future precompiles inherit the protection by default rather than requiring each precompile author to remember to add it individually.

### Proof of Concept
1. Deploy a minimal attacker contract with a function:
```solidity
function attack(address precompile, bytes calldata data) external payable {
    (bool ok, ) = precompile.call{value: msg.value}(data);
    require(ok);
}
```
2. Call `attack(STAKING_PRECOMPILE_ADDRESS, abi.encodeWithSelector(delegateSelector, delegatorAddr, validatorAddr, delegateAmount))` with `msg.value = 1 ether`, where `delegateAmount` is a small/independent uint256 unrelated to the attached `msg.value`.
3. Observe: `staking.Execute` (`precompiles/staking/staking.go:99-139`) processes the `Delegate` call using the `delegateAmount` argument only; it never inspects `contract.Value()`. The EVM's value-transfer machinery has already credited `1 ether` to the staking precompile's address balance in the StateDB before `Run` executed.
4. Confirm (via `x/vm` `GetBalance` query on the staking precompile address) that the `1 ether` balance now sits at the precompile address with no corresponding `withdraw`/`sweep` function exposed by `StakingI.sol`, `precompiles/staking/staking.go`, or any governance/keeper hook in the repository — the funds are permanently locked exactly as in the `erc20` precompile's addressed (but here unaddressed) scenario.

*Note: full confirmation that no downstream reconciliation exists (e.g., an automatic keeper hook sweeping precompile-address balances) could not be exhaustively verified across the entire `x/vm` keeper and module `BeginBlock`/`EndBlock` code due to index size limits; a Devin session with full repository access would allow tracing every keeper hook to rule out an undiscovered recovery path.*

### Citations

**File:** precompiles/erc20/erc20.go (L148-155)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}
```

**File:** precompiles/werc20/tx.go (L26-57)
```go
// Deposit handles the payable deposit function. It retrieves the deposited amount
// and sends it back to the sender using the bank keeper.
func (p Precompile) Deposit(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
) ([]byte, error) {
	caller := contract.Caller()
	depositedAmount := contract.Value()

	callerAccAddress := sdk.AccAddress(caller.Bytes())
	precompileAccAddr := sdk.AccAddress(p.Address().Bytes())

	// Send the coins back to the sender
	if err := p.BankKeeper.SendCoins(
		ctx,
		precompileAccAddr,
		callerAccAddress,
		sdk.NewCoins(sdk.Coin{
			Denom:  evmtypes.GetEVMCoinExtendedDenom(),
			Amount: math.NewIntFromBigInt(depositedAmount.ToBig()),
		}),
	); err != nil {
		return nil, err
	}

	if err := p.EmitDepositEvent(ctx, stateDB, caller, depositedAmount.ToBig()); err != nil {
		return nil, err
	}

	return nil, nil
}
```

**File:** precompiles/common/precompile.go (L46-126)
```go
// Run prepare the native context to execute native action for stateful precompile,
// it manages the snapshot and revert of the multi-store.
func (p Precompile) RunNativeAction(evm *vm.EVM, contract *vm.Contract, action NativeAction) ([]byte, error) {
	bz, err := p.runNativeAction(evm, contract, action)
	if err != nil {
		return ReturnRevertError(evm, err)
	}

	return bz, nil
}

func (p Precompile) runNativeAction(evm *vm.EVM, contract *vm.Contract, action NativeAction) (bz []byte, err error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.New(ErrNotRunInEvm)
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

	return bz, nil
}
```

**File:** precompiles/staking/staking.go (L99-139)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch method.Name {
	// Staking transactions
	case CreateValidatorMethod:
		bz, err = p.CreateValidator(ctx, contract, stateDB, method, args)
	case EditValidatorMethod:
		bz, err = p.EditValidator(ctx, contract, stateDB, method, args)
	case DelegateMethod:
		bz, err = p.Delegate(ctx, contract, stateDB, method, args)
	case UndelegateMethod:
		bz, err = p.Undelegate(ctx, contract, stateDB, method, args)
	case RedelegateMethod:
		bz, err = p.Redelegate(ctx, contract, stateDB, method, args)
	case CancelUnbondingDelegationMethod:
		bz, err = p.CancelUnbondingDelegation(ctx, contract, stateDB, method, args)
	// Staking queries
	case DelegationMethod:
		bz, err = p.Delegation(ctx, contract, method, args)
	case UnbondingDelegationMethod:
		bz, err = p.UnbondingDelegation(ctx, contract, method, args)
	case ValidatorMethod:
		bz, err = p.Validator(ctx, method, contract, args)
	case ValidatorsMethod:
		bz, err = p.Validators(ctx, method, contract, args)
	case RedelegationMethod:
		bz, err = p.Redelegation(ctx, method, contract, args)
	case RedelegationsMethod:
		bz, err = p.Redelegations(ctx, method, contract, args)
	default:
		return nil, fmt.Errorf(cmn.ErrUnknownMethod, method.Name)
	}

	return bz, err
}
```

**File:** precompiles/distribution/distribution.go (L97-104)
```go
func (p Precompile) Run(evm *vm.EVM, contract *vm.Contract, readonly bool) ([]byte, error) {
	return p.RunNativeAction(evm, contract, func(ctx sdk.Context) ([]byte, error) {
		return p.Execute(ctx, evm.StateDB, contract, readonly)
	})
}

func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
```

**File:** precompiles/slashing/slashing.go (L98-122)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch method.Name {
	// slashing transactions
	case UnjailMethod:
		bz, err = p.Unjail(ctx, method, stateDB, contract, args)
	// slashing queries
	case GetSigningInfoMethod:
		bz, err = p.GetSigningInfo(ctx, method, contract, args)
	case GetSigningInfosMethod:
		bz, err = p.GetSigningInfos(ctx, method, contract, args)
	case GetParamsMethod:
		bz, err = p.GetParams(ctx, method, contract, args)
	default:
		return nil, fmt.Errorf(cmn.ErrUnknownMethod, method.Name)
	}

	return bz, err
}
```

**File:** precompiles/bank/bank.go (L115-136)
```go
// Execute executes the precompiled contract bank query methods defined in the ABI.
func (p Precompile) Execute(ctx sdk.Context, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte
	switch method.Name {
	// Bank queries
	case BalancesMethod:
		bz, err = p.Balances(ctx, method, args)
	case TotalSupplyMethod:
		bz, err = p.TotalSupply(ctx, method, args)
	case SupplyOfMethod:
		bz, err = p.SupplyOf(ctx, method, args)
	default:
		return nil, fmt.Errorf(cmn.ErrUnknownMethod, method.Name)
	}

	return bz, err
}
```

**File:** precompiles/ics20/ics20.go (L97-121)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch method.Name {
	// ICS20 transactions
	case TransferMethod:
		bz, err = p.Transfer(ctx, contract, stateDB, method, args)
	// ICS20 queries
	case DenomMethod:
		bz, err = p.Denom(ctx, contract, method, args)
	case DenomsMethod:
		bz, err = p.Denoms(ctx, contract, method, args)
	case DenomHashMethod:
		bz, err = p.DenomHash(ctx, contract, method, args)
	default:
		return nil, fmt.Errorf(cmn.ErrUnknownMethod, method.Name)
	}

	return bz, err
}
```
