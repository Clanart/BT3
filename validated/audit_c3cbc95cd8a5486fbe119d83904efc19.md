This is the key finding: `RegisterERC20` is **permissionless** when `params.PermissionlessRegistration` is true [1](#0-0) , meaning any unprivileged user can deploy their own ERC20 contract and register it as a native token pair via `registerERC20` / `SetToken` [2](#0-1) .

### Title
Self-destructible native ERC20 contracts allow permanent freezing of escrowed native coins via silent token-pair deletion - (File: x/erc20/keeper/msg_server.go)

### Summary
When a "native ERC20" token pair's underlying contract self-destructs, `ConvertERC20`/`ConvertCoin` silently delete the token pair and return `nil, nil` without reconciling or returning any coins that were already escrowed in the `erc20` module account, permanently freezing those funds.

### Finding Description
`ConvertERC20` and `ConvertCoin` check whether the paired ERC20 contract still has code; if not (self-destructed via `DeleteAccount`, which clears code, storage, and code hash [3](#0-2) ), they call `k.DeleteTokenPair(ctx, pair)` and return `nil, nil`, treating deletion as a successful, non-erroring operation [4](#0-3) [5](#0-4) . `DeleteTokenPair` only removes the pair/denom/ERC20/allowance index entries; it performs no accounting reconciliation of coins already escrowed in the `erc20` module account from prior `ConvertERC20` calls (`convertERC20IntoCoinsForNativeToken` transfers ERC20 tokens to `types.ModuleAddress` and mints/sends an equal amount of native coins to the user) [6](#0-5) [7](#0-6) .

This directly mirrors the report's core defect: an "asset change" (here, the underlying ERC20 contract disappearing/being replaced) invalidates cached backing assumptions, and cleanup code (`_collectFees`-equivalent = `DeleteTokenPair`) does not fully reset/reconcile the state that depended on the old asset. Since `RegisterERC20` is permissionless when `PermissionlessRegistration` is enabled, an unprivileged attacker can deploy a self-destructible ERC20 contract, register it as a native token pair, get users (or their own second address) to `ConvertERC20` into native coins (escrowing tokens in the module account), then self-destruct the contract. All ERC20 tokens escrowed in the module account become permanently stuck (the contract's storage/balances are wiped and the code is gone, so they can never be retrieved), while the previously-minted native coins remain in user wallets — this is not itself a supply violation, but the escrowed ERC20 balance backing them is destroyed with no supply/escrow adjustment, and any user still holding the ERC20 side (or attempting the reverse `ConvertCoin`) loses access permanently once the pair is deleted, since there is no path back to the contract.

### Impact Explanation
This falls under "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds, contract balances, escrowed assets... or token-pair-backed balances" — escrowed ERC20 tokens (or, symmetrically, escrowed native coins in `ConvertCoinNativeERC20`, which sends coins to the module account before attempting `transfer` from the module address on a now-selfdestructed contract) become unrecoverable, and the accounting linking coin supply to ERC20 backing is permanently broken with no admin recovery path (token pair state is deleted).

### Likelihood Explanation
Medium-to-High: this requires `PermissionlessRegistration` to be enabled (a governance-controlled toggle, but once enabled the exploit path itself is fully unprivileged) and requires an attacker to deploy a trivially self-destructible contract and get value escrowed against it (e.g., their own conversion, or luring victims into converting into a token that is later killed).

### Recommendation
Do not silently delete token pairs and return success on self-destruction detection. Instead: (1) check and reconcile any module-account-escrowed balances tied to the pair before deletion, refunding/burning coins so the 1:1 backing invariant is preserved; (2) consider disabling further `ConvertCoin`/`ConvertERC20` operations for the pair (rather than deleting it outright) so escrow state remains auditable and any stuck funds are visible/handled deliberately; (3) emit a distinct error/event rather than treating the deletion as a no-op success.

### Proof of Concept
Not independently executed against a live node; PoC would be: (1) enable/observe `PermissionlessRegistration`; (2) deploy an ERC20 contract with a callable self-destruct function, register it via `MsgRegisterERC20`; (3) call `ConvertERC20` to escrow tokens into the `erc20` module account and mint native coins; (4) self-destruct the contract; (5) call `ConvertERC20`/`ConvertCoin` again to observe `DeleteTokenPair` fire and confirm the module-account-escrowed ERC20 balance is now permanently unreachable, using the test harness pattern shown in `tests/integration/x/erc20/test_ibc_callback.go` for triggering the self-destruct+conversion path [8](#0-7) .

**Uncertainty**: I could not verify from the indexed code whether `PermissionlessRegistration` defaults to `true` or `false` in this chain's genesis, nor find the exact governance default for it — this affects the practical likelihood/trigger conditions and should be confirmed in a full repo checkout. I also could not confirm whether any additional reconciliation of module-account escrow occurs elsewhere (e.g., in an end-blocker) that might mitigate this.

### Citations

**File:** x/erc20/keeper/msg_server.go (L41-53)
```go
	// Check ownership and execute conversion
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L63-140)
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

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

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

**File:** x/erc20/keeper/msg_server.go (L207-220)
```go
	// Check ownership and execute conversion
	switch {
	case pair.IsNativeERC20():
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L324-335)
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

**File:** x/vm/keeper/statedb.go (L243-295)
```go
// DeleteAccount handles contract's suicide call:
// - clear balance
// - remove code
// - remove states
// - remove the code hash
// - remove auth account
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum contracts can be self-destructed
	if !k.IsContract(ctx, addr) {
		return errors.New("only smart contracts can be self-destructed")
	}

	// set account to a base account to set the whole balance as spendable
	baseAccount := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	k.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(cosmosAddr, baseAccount.GetPubKey(), baseAccount.GetAccountNumber(), baseAccount.GetSequence()))

	// clear balance
	if err := k.SetBalance(ctx, addr, new(uint256.Int)); err != nil {
		return err
	}

	var keys []common.Hash

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		keys = append(keys, key)
		return true
	})

	for _, key := range keys {
		k.DeleteState(ctx, addr, key)
	}

	// clear code hash
	k.DeleteCodeHash(ctx, addr)

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.Logger(ctx).Debug(
		"account suicided",
		"ethereum-address", addr.Hex(),
		"cosmos-address", cosmosAddr.String(),
	)

	return nil
}
```

**File:** x/erc20/keeper/token_pairs.go (L110-117)
```go
// DeleteTokenPair removes a token pair.
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```

**File:** tests/integration/x/erc20/test_ibc_callback.go (L575-614)
```go
		},
		{
			name: "err - self-destructed contract",
			malleate: func() {
				// Register Token Pair for testing
				contractAddr, err := s.setupRegisterERC20Pair(contractMinterBurner)
				s.Require().NoError(err, "failed to register pair")
				ctx = s.network.GetContext()
				id := s.network.App.GetErc20Keeper().GetTokenPairID(ctx, contractAddr.String())
				pair, _ = s.network.App.GetErc20Keeper().GetTokenPair(ctx, id)
				s.Require().NotNil(pair)

				// self destruct the token
				err = s.network.App.GetEVMKeeper().DeleteAccount(s.network.GetContext(), contractAddr)
				s.Require().NoError(err)

				sender = sdk.AccAddress(senderPk.PubKey().Address())

				// Fund receiver account with ATOM, ERC20 coins and IBC vouchers
				// We do this since we are interested in the conversion portion w/ OnRecvPacket
				err = testutil.FundAccount(
					ctx,
					s.network.App.GetBankKeeper(),
					sender,
					sdk.NewCoins(
						sdk.NewCoin(pair.Denom, math.NewInt(100)),
					),
				)
				s.Require().NoError(err)

				ack = channeltypes.NewErrorAcknowledgement(errors.New("error"))
				data = transfertypes.NewFungibleTokenPacketData(pair.Denom, "100", sender.String(), receiver.String(), "")
			},
			expERC20: big.NewInt(0),
			expPass:  false,
			expErrorEvents: func() {
				event := ctx.EventManager().Events()[len(ctx.EventManager().Events())-1]
				s.Require().Equal(event.Type, types.EventTypeFailedConvertERC20)
			},
		},
```
