### Title
Native token value sent to non-ERC20 precompiles (staking, distribution, gov, slashing, ics20, bank) is permanently stuck - ([File: precompiles/staking/staking.go], [File: precompiles/distribution/distribution.go], [File: precompiles/gov/gov.go], [File: precompiles/slashing/slashing.go], [File: precompiles/ics20/ics20.go])

### Summary
The external report's root cause is a function that can legitimately receive `msg.value` but has no logic to consume, forward, or refund it, causing funds sent to it to become permanently stuck. Cosmos EVM has already recognized and patched this exact bug class for the `erc20` precompile, which explicitly rejects any non-zero `contract.Value()` before execution. However, every other stateful precompile (`staking`, `distribution`, `gov`, `slashing`, `ics20`, `bank`) is missing this guard, even though ordinary EVM value-transfer semantics allow a caller to attach native-token value to any `CALL` targeting these precompile addresses.

### Finding Description
The `erc20` precompile's `Execute` explicitly documents and guards against this bug class: [1](#0-0) 

```go
// ERC20 precompiles cannot receive funds because they are not managed by an
// EOA and will not be possible to recover funds sent to an instance of
// them.This check is a safety measure because currently funds cannot be
// received due to the lack of a fallback handler.
if value := contract.Value(); value.Sign() == 1 {
    return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
}
```

The `werc20` precompile likewise handles received value correctly by immediately forwarding it back via `BankKeeper.SendCoins` in `Deposit`, so the caller's balance is preserved [2](#0-1) .

No equivalent guard or value-consumption logic exists in the other stateful precompiles' `Execute` functions:
- staking: [3](#0-2) 
- distribution: [4](#0-3) 
- gov: [5](#0-4) 
- slashing: [6](#0-5) 
- ics20: [7](#0-6) 

None of these check `contract.Value()`. Meanwhile, the EVM's call value-transfer mechanism is standard and unconditional: `BlockContext.Transfer` is wired to `core.Transfer`, which moves balance from caller to callee (the precompile address) for any nonzero call value regardless of whether the target is a precompile or a regular contract [8](#0-7) . The ante-handler's `CanTransfer` check only verifies the sender has sufficient balance to cover the attached value; it does not restrict which addresses may receive it [9](#0-8) .

Because the staking/distribution/gov/slashing/ics20/bank precompiles are system addresses with no controlling private key and no withdrawal/fallback mechanism, any native-token value attached to a call targeting them (e.g., a `delegate`, `vote`, `unjail`, `transfer` ICS20 call, etc., all of which take the transfer amount as an explicit `uint256` argument rather than `msg.value`) is silently credited to the precompile's account balance and can never be moved out again — the balance sits in the bank module against an address nobody controls.

### Impact Explanation
This matches the "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds" impact category. Any unprivileged EOA can trigger the loss by crafting an ordinary Ethereum transaction with `to = <staking/distribution/gov/slashing/ics20/bank precompile address>` and a nonzero `value` field alongside valid calldata for any transaction method. The value is deducted from the sender's spendable native balance and irrecoverably locked at the precompile address, since there is no code path in any of these precompiles that spends, forwards, or refunds `contract.Value()`.

### Likelihood Explanation
Likelihood is high in terms of reachability (any user can trigger it with a single transaction and no special privileges), though the impact is generally self-inflicted (the caller loses their own funds due to their own transaction construction) rather than an attacker draining a third party. It does, however, meet the letter of "permanent locking of user funds" and mirrors exactly the fix precedent already applied to `erc20`/`werc20` in this codebase, indicating the maintainers consider this bug class worth guarding against for precompiles.

### Recommendation
Add the same guard used in `precompiles/erc20/erc20.go` to the `Execute` functions of `staking`, `distribution`, `gov`, `slashing`, `ics20`, and `bank` precompiles: reject any call where `contract.Value().Sign() == 1` unless the specific method is designed to consume/forward that value (as `werc20.Deposit` does).

### Proof of Concept
1. Construct a raw Ethereum transaction with `to` set to the staking precompile address (`0x0000000000000000000000000000000000000800`), `value` set to a nonzero amount, and `data` set to a valid ABI-encoded call to any staking method (e.g., `unjail`-style query or `delegate` with the `amount` argument set to 0, keeping the tx `value` field nonzero and unrelated).
2. Submit the transaction from any funded EOA.
3. Observe that `core.Transfer` debits the sender's balance and credits the precompile address's balance by `value` before `Run`/`Execute` is invoked [8](#0-7) .
4. Confirm the staking `Execute` proceeds without checking `contract.Value()` [3](#0-2) , so the call succeeds and the attached value remains permanently credited to the precompile's account with no mechanism to retrieve it.

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

**File:** precompiles/werc20/tx.go (L28-57)
```go
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

**File:** precompiles/distribution/distribution.go (L103-104)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
```

**File:** precompiles/gov/gov.go (L98-143)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch method.Name {
	// gov transactions
	case VoteMethod:
		bz, err = p.Vote(ctx, contract, stateDB, method, args)
	case VoteWeightedMethod:
		bz, err = p.VoteWeighted(ctx, contract, stateDB, method, args)
	case SubmitProposalMethod:
		bz, err = p.SubmitProposal(ctx, contract, stateDB, method, args)
	case DepositMethod:
		bz, err = p.Deposit(ctx, contract, stateDB, method, args)
	case CancelProposalMethod:
		bz, err = p.CancelProposal(ctx, contract, stateDB, method, args)

	// gov queries
	case GetVoteMethod:
		bz, err = p.GetVote(ctx, method, contract, args)
	case GetVotesMethod:
		bz, err = p.GetVotes(ctx, method, contract, args)
	case GetDepositMethod:
		bz, err = p.GetDeposit(ctx, method, contract, args)
	case GetDepositsMethod:
		bz, err = p.GetDeposits(ctx, method, contract, args)
	case GetTallyResultMethod:
		bz, err = p.GetTallyResult(ctx, method, contract, args)
	case GetProposalMethod:
		bz, err = p.GetProposal(ctx, method, contract, args)
	case GetProposalsMethod:
		bz, err = p.GetProposals(ctx, method, contract, args)
	case GetParamsMethod:
		bz, err = p.GetParams(ctx, method, contract, args)
	case GetConstitutionMethod:
		bz, err = p.GetConstitution(ctx, method, contract, args)
	default:
		return nil, fmt.Errorf(cmn.ErrUnknownMethod, method.Name)
	}

	return bz, err
}
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

**File:** x/vm/keeper/state_transition.go (L44-47)
```go
	ctx = k.SetConsensusParamsInCtx(ctx)
	blockCtx := vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    core.Transfer,
```

**File:** ante/evm/07_can_transfer.go (L18-51)
```go
// CanTransfer checks if the sender is allowed to transfer funds according to the EVM block
func CanTransfer(
	ctx sdk.Context,
	evmKeeper anteinterfaces.EVMKeeper,
	msg core.Message,
	baseFee *big.Int,
	params evmtypes.Params,
	isLondon bool,
) error {
	if isLondon && msg.GasFeeCap.Cmp(baseFee) < 0 {
		return errorsmod.Wrapf(
			errortypes.ErrInsufficientFee,
			"max fee per gas less than block base fee (%s < %s)",
			msg.GasFeeCap, baseFee,
		)
	}

	// check that caller has enough balance to cover asset transfer for **topmost** call
	// NOTE: here the gas consumed is from the context with the infinite gas meter
	convertedValue, err := utils.Uint256FromBigInt(msg.Value)
	if err != nil {
		return err
	}
	if msg.Value.Sign() > 0 && evmKeeper.GetAccount(ctx, msg.From).Balance.Cmp(convertedValue) < 0 {
		return errorsmod.Wrapf(
			errortypes.ErrInsufficientFunds,
			"failed to transfer %s from address %s using the EVM block context transfer function",
			msg.Value,
			msg.From,
		)
	}

	return nil
}
```
