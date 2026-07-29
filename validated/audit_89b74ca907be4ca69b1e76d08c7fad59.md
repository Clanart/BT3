## Analog Identified: Reentrant `ConvertERC20` minting via attacker-controlled ERC20 contract logic

### Title
Reentrant/self-reported ERC20 accounting allows unauthorized minting of native coins in `ConvertERC20` — (File: `x/erc20/keeper/msg_server.go`)

### Summary
The original Backd finding shows that an ERC777 hook fired mid-transfer lets an attacker re-enter a deposit function before a cap-check's state snapshot is finalized, so the check compares stale state and under-counts value. The Cosmos EVM analog lives in `x/erc20/keeper/msg_server.go`'s `convertERC20IntoCoinsForNativeToken`, the handler behind `MsgConvertERC20` used to mint native Cosmos coins 1:1 against ERC20 balances for `OWNER_EXTERNAL` ("native ERC20") token pairs [1](#0-0) . This function snapshots the module's ERC20 balance, then invokes the attacker-supplied ERC20 contract's `transfer()` through `k.evmKeeper.CallEVMWithData(...)`, and only after the call returns re-reads the balance to validate an invariant before minting coins [2](#0-1) .

### Finding Description
Token pairs can be registered permissionlessly: when `params.PermissionlessRegistration` is `true` (a normal, non-privileged governance parameter state, exercised in tests as "ok - non-governance, permissionless true"), any unprivileged account can register **any** externally-owned ERC20 contract via `MsgRegisterERC20` without further validation of its bytecode [3](#0-2) [4](#0-3) .

Once registered as `OWNER_EXTERNAL` (`IsNativeERC20`) [5](#0-4) , `ConvertERC20` trusts that contract's self-reported `balanceOf`/`transfer` semantics as ground truth for minting native coins:
- `balanceToken` is captured before the external call.
- `CallEVMWithData` executes the attacker's contract code as an arbitrary top-level EVM message (hooks, callbacks, or any logic the attacker wants can run here) [6](#0-5) .
- Only the balance delta reported by that same attacker-controlled contract is checked before `bankKeeper.MintCoins` and `SendCoinsFromModuleToAccount` execute [2](#0-1) .

Because the "escrow" and "receiver" balances used for the invariant check are both read from the very ERC20 contract the attacker deployed and controls, there is no external, trust-minimized signal being validated — this is the same class of bug as the ERC777 case: a value-affecting check is evaluated against state that can be manipulated by attacker-controlled code executed *during* the very call the check is meant to validate. In the ERC777 report this was a reentrant hook invalidating a pre-read cap variable; here a fully attacker-authored `transfer()`/`balanceOf()` pair can be written to unconditionally satisfy the before/after invariant (e.g., always reporting balances offset by exactly `msg.Amount`) regardless of whether any real value changed hands, or can reenter further Cosmos-SDK-reachable surfaces (e.g. IBC transfer's `Keeper.Transfer`, which itself calls `k.erc20Keeper.ConvertERC20` again for insufficient-bank-balance cases [7](#0-6) ) from inside the nested EVM call, minting coins multiple times off of a single underlying token movement.

### Impact Explanation
A successful exploit lets an unprivileged attacker mint unlimited native Cosmos coins under the `erc20:<contract>` denom created by `RegisterERC20`, backed by no real value, since `bankKeeper.MintCoins`/`SendCoinsFromModuleToAccount` execute unconditionally once the (attacker-forgeable) balance check passes [8](#0-7) . This is unauthorized minting of spendable user-facing value, matching the Critical "unauthorized minting" impact class.

### Likelihood Explanation
Reachable by any unprivileged EVM user without governance or validator cooperation, provided `PermissionlessRegistration` is enabled (a supported, tested configuration, not merely a privileged/gov-only path) [9](#0-8) . The attacker fully controls the deployed contract's bytecode, so no race condition or gas-timing luck is required — the malicious contract can be engineered deterministically to pass the invariant check every time.

### Recommendation
Do not derive minting authorization purely from values returned by an externally-owned, attacker-deployable ERC20 contract. Options: (1) enforce actual balance transfer via the bank/EVM statedb's authoritative account state rather than a re-queried `balanceOf` call on the same untrusted contract; (2) require token pairs backing native-coin minting to go through a governance-controlled or otherwise vetted registration path, disabling permissionless mint-authorization for `OWNER_EXTERNAL` pairs; (3) add reentrancy guards around `ConvertERC20`/`ConvertCoinNativeERC20` so a nested `MsgConvertERC20` (e.g. triggered transitively through IBC transfer's auto-convert path) cannot execute while an outer conversion for the same token pair/sender is in flight.

### Proof of Concept
1. Deploy a malicious "ERC20" contract that implements `transfer()` to always return `true` and implements `balanceOf(moduleAddr)`/`balanceOf(receiver)` to return values that satisfy `expToken := balanceToken + tokens` and `expCoin := balanceCoin + amount` unconditionally, regardless of any real balance movement.
2. Call `MsgRegisterERC20` with this contract (works if `PermissionlessRegistration=true`, per `x/erc20/keeper/msg_server.go` `RegisterERC20`).
3. Call `MsgConvertERC20` repeatedly for this contract/denom; each call passes the invariant checks in `convertERC20IntoCoinsForNativeToken` and mints real native coins to the attacker's receiver address, with no underlying value ever escrowed.

### Citations

**File:** x/erc20/keeper/msg_server.go (L63-95)
```go
// convertERC20IntoCoinsForNativeToken handles the erc20 conversion for a native erc20 token
// pair:
//   - escrow tokens on module account
//   - mint coins on bank module
//   - send minted coins to the receiver
//   - check if coin balance increased by amount
//   - check if token balance decreased by amount
//   - check for unexpected `Approval` event in logs
func (k Keeper) convertERC20IntoCoinsForNativeToken(
	ctx sdk.Context,
	pair types.TokenPair,
	msg *types.MsgConvertERC20,
	receiver sdk.AccAddress,
	sender common.Address,
) (*types.MsgConvertERC20Response, error) {
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()
	balanceCoin := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceToken == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/msg_server.go (L113-140)
```go
	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-350)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}
```

**File:** x/erc20/keeper/proposals.go (L16-42)
```go
// RegisterERC20 creates a Cosmos coin and registers the token pair between the
// coin and the ERC20
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
}
```

**File:** x/erc20/types/token_pair.go (L67-70)
```go
// IsNativeERC20 returns true if the owner of the ERC20 contract is an EOA.
func (tp TokenPair) IsNativeERC20() bool {
	return tp.ContractOwner == OWNER_EXTERNAL
}
```

**File:** x/vm/keeper/call_evm.go (L48-93)
```go
// CallEVMWithData performs a smart contract method call using contract data.
func (k Keeper) CallEVMWithData(
	ctx sdk.Context,
	from common.Address,
	contract *common.Address,
	data []byte,
	commit bool,
	gasCap *big.Int,
) (*types.MsgEthereumTxResponse, error) {
	nonce, err := k.accountKeeper.GetSequence(ctx, from.Bytes())
	if err != nil {
		return nil, err
	}

	msg := core.Message{
		From:       from,
		To:         contract,
		Nonce:      nonce,
		Value:      big.NewInt(0),
		GasLimit:   config.DefaultGasCap,
		GasPrice:   big.NewInt(0),
		GasTipCap:  big.NewInt(0),
		GasFeeCap:  big.NewInt(0),
		Data:       data,
		AccessList: ethtypes.AccessList{},
	}

	// Use a cache context so that a reverting EVM call does not corrupt the
	// parent gas meter. On success we commit the cache and charge the actual
	// gas used; on revert we discard the cache and leave the parent meter
	// untouched (matching DerivedEVMCallWithData semantics).
	tmpCtx, commitState := ctx.CacheContext()
	res, err := k.ApplyMessage(tmpCtx, msg, nil, commit, true)
	if err != nil {
		return nil, err
	}

	if res.Failed() {
		return res, errorsmod.Wrap(types.ErrVMExecution, res.VmError)
	}

	commitState()
	ctx.GasMeter().ConsumeGas(res.GasUsed, "apply evm message")

	return res, nil
}
```

**File:** x/ibc/transfer/keeper/msg_server.go (L90-104)
```go
	// Only convert if the pair is a native ERC20
	// only convert the remaining difference
	difference := msg.Token.Amount.Sub(balance.Amount)

	msgConvertERC20 := erc20types.NewMsgConvertERC20(
		difference,
		sender,
		pair.GetERC20Contract(),
		common.BytesToAddress(sender.Bytes()),
	)

	// Use MsgConvertERC20 to convert the ERC20 to a Cosmos IBC Coin
	if _, err := k.erc20Keeper.ConvertERC20(ctx, msgConvertERC20); err != nil {
		return nil, err
	}
```

**File:** tests/integration/x/erc20/test_proposals.go (L150-155)
```go
		{
			"ok - non-governance, permissionless true",
			func() {},
			s.keyring.GetAccAddr(0).String(),
			true,
		},
```
