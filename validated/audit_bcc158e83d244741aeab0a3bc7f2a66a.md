### Title
Self-destructing a registered ERC20 token pair contract permanently destroys escrowed collateral while previously-minted native coins remain in circulation, creating unbacked/duplicated value - (File: x/erc20/keeper/msg_server.go)

### Summary
This is the Cosmos EVM analog of the Trail of Bits "Folio owner can rug pull DTF shareholders" bug class: a party is able to unilaterally destroy the backing/collateral of a fungible-value representation without a corresponding reduction of the liability (the outstanding shares/coins), silently corrupting the accounting invariant that the value of circulating units equals the value of the backing basket/collateral. In the DTF report, `Role::Owner` removed basket tokens without adjusting share value. In `x/erc20`, any unprivileged actor who controls a registered native-ERC20 token-pair contract can self-destruct it, which wipes out the ERC20 balance escrowed in the module account (the collateral backing already-minted native coins), while the module simply deletes the token-pair record instead of reversing/burning the outstanding coins.

### Finding Description
When a native-ERC20 token pair is registered, users call `MsgConvertERC20` to escrow ERC20 tokens into the `erc20` module account and mint an equal amount of native Cosmos coins to a receiver: [1](#0-0) 

If the underlying ERC20 contract is later self-destructed (any owner/deployer of a Solidity contract can add and call a `selfdestruct`), `x/vm`'s `DeleteAccount` unconditionally clears the account's balance, storage, and code: [2](#0-1) 

This wipes out *all* balances tracked in that ERC20 contract's storage, including the tokens the `erc20` module escrowed on behalf of coin holders in `convertERC20IntoCoinsForNativeToken`. The next time anyone calls `ConvertERC20`/`ConvertCoin` on that pair, the module detects the account has no code and simply deletes the token-pair mapping, returning `nil` with no error and, critically, **no burn of the already-minted native coins**: [3](#0-2) [4](#0-3) 

`DeleteTokenPair` only removes the ID/ERC20-map/denom-map/allowances - it performs no supply reconciliation: [5](#0-4) 

The project's own integration tests confirm this exact "self-destructed contract" flow silently deletes the token pair without addressing outstanding coin supply: [6](#0-5) [7](#0-6) 

Result: native coins minted 1:1 against the ERC20 escrow before the self-destruct remain fully valid, transferable, spendable bank coins forever, while their backing collateral is irreversibly zeroed. This breaks the fundamental `x/erc20` invariant (stated in the module's own docs/tests) that native-coin supply is always backed 1:1 by escrowed ERC20 tokens.

### Impact Explanation
This is a critical, irreversible accounting corruption of spendable user value: an unprivileged actor can mint real, freely transferable native Cosmos coins backed by ERC20 collateral, then destroy that collateral, effectively duplicating value out of thin air with no burn/reconciliation. Anyone who later receives, holds, or trades those "orphaned" coins is holding value that is unbacked, and if `PermissionlessRegistration` is enabled (a supported production configuration) or if a governance-registered pair's owner later adds and triggers a self-destruct, no privileged/trusted role is required to execute the attack end-to-end. This matches the allowed-impact criteria of unauthorized minting/duplication/irreversible accounting corruption of spendable user value across native balances and ERC20 representations.

### Likelihood Explanation
Moderate-to-high: the attacker needs to (1) get a token pair registered for an ERC20 contract they control (permissionless if enabled, or otherwise requires the contract to already be a registered pair - e.g., attacker acquires/deploys a contract that is later self-destructible), (2) convert tokens to native coins to lock in value, and (3) call `selfdestruct`. All three steps are ordinary, unprivileged transaction flows (`MsgRegisterERC20`, `MsgConvertERC20`, and a normal EVM contract call) with no special role checks beyond the standard `PermissionlessRegistration`/`IsERC20Enabled` gates.

### Recommendation
- **Short term:** Before deleting a token pair due to a self-destructed/code-less contract, check the outstanding bank supply of `pair.Denom`. If non-zero, either (a) refuse to delete the pair and instead mark it permanently disabled/frozen without erasing accounting linkage, or (b) burn/quarantine the outstanding supply (via a mechanism that fairly compensates or clearly documents the loss) instead of silently deleting the mapping and returning success.
- **Long term:** Add an invariant check (e.g., in `x/erc20`'s invariants or in `EndBlock`) asserting that for every native-ERC20 token pair, `bank` supply of `pair.Denom` never exceeds the ERC20 contract's escrowed balance held by the module account, and halt/alert if violated. Consider preventing conversion into coins for ERC20 contracts capable of self-destruct, or require an escrow-balance re-verification/timelock before permanently removing a token pair.

### Proof of Concept
1. Enable/assume `PermissionlessRegistration = true` in `x/erc20` params (or use a governance-registered pair whose contract the attacker controls).
2. Attacker deploys a malicious ERC20 contract with a mint function restricted to itself and a public/owner-only `selfdestruct` function.
3. Attacker calls `MsgRegisterERC20` to register the contract as a token pair (`x/erc20/keeper/msg_server.go:326`).
4. Attacker mints, e.g., 1,000,000 tokens to itself, then calls `MsgConvertERC20` (`x/erc20/keeper/msg_server.go:26-61`) to escrow the tokens into the module account and mint 1,000,000 native coins of `pair.Denom` to itself.
5. Attacker transfers/spends/IBC-sends the newly minted native coins to other parties or into DeFi/staking, realizing real economic value.
6. Attacker calls `selfdestruct` on the ERC20 contract, triggering `x/vm`'s `DeleteAccount` (`x/vm/keeper/statedb.go:249-295`), which zeroes the contract's storage - including the module's escrowed balance.
7. Attacker (or anyone) calls `MsgConvertERC20`/`MsgConvertCoin` again on the pair; the module detects `acc == nil || !acc.HasCodeHash()` and calls `DeleteTokenPair`, returning success with no burn (`x/erc20/keeper/msg_server.go:44-52`, `210-219`).
8. Result: 1,000,000 native coins remain circulating/spendable with zero backing ERC20 collateral, a permanent, irreversible supply/collateral mismatch.

### Citations

**File:** x/erc20/keeper/msg_server.go (L41-60)
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

		return k.convertERC20IntoCoinsForNativeToken(ctx, pair, msg, receiver, sender) // case 2.1
	} else if pair.IsNativeCoin() {
		return nil, types.ErrNativeConversionDisabled
	}

	return nil, types.ErrUndefinedOwner
```

**File:** x/erc20/keeper/msg_server.go (L86-140)
```go
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

**File:** tests/integration/x/erc20/test_msg_server.go (L657-672)
```go
				acc := s.network.App.GetEVMKeeper().GetAccountWithoutBalance(s.network.GetContext(), contractAddr)
				if tc.selfdestructed {
					s.Require().Nil(acc, "expected contract to be destroyed")
				} else {
					s.Require().NotNil(acc)
				}

				isContract := s.network.App.GetEVMKeeper().IsContract(s.network.GetContext(), contractAddr)
				if tc.selfdestructed || !isContract {
					id := s.network.App.GetErc20Keeper().GetTokenPairID(s.network.GetContext(), contractAddr.String())
					_, found := s.network.App.GetErc20Keeper().GetTokenPair(s.network.GetContext(), id)
					s.Require().False(found)
				} else {
					s.Require().Equal(cosmosBalance.Amount, math.NewInt(tc.mint-tc.transfer))
					s.Require().Equal(evmTokenBalanceAfter.(*big.Int).Int64(), math.NewInt(tc.transfer).Int64())
				}
```

**File:** tests/integration/x/erc20/test_ibc_callback.go (L576-614)
```go
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
