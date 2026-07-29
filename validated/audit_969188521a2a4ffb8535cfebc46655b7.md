### Title
Self-destruct of a registered native ERC20 contract permanently destroys other holders' token balances before conversion, silently orphaning the token pair - ([File: x/erc20/keeper/msg_server.go])

### Summary
`RegisterERC20` allows registering any deployed ERC20 contract as a native token pair — permissionlessly when `params.PermissionlessRegistration` is enabled, or via governance otherwise [1](#0-0) . Any account controlling such a registered contract can trigger `SELFDESTRUCT`, which the `x/vm` keeper implements via `DeleteAccount`: it zeroes the balance, deletes **all** contract storage (including every holder's ERC20 balance mapping), and removes the code hash [2](#0-1) . The next `ConvertERC20`/`ConvertCoin` call on that pair detects the missing code hash and unconditionally deletes the token pair as a "cleanup" step, without ever checking whether other holders still had unconverted ERC20 balances that were just wiped out [3](#0-2) [4](#0-3) .

### Finding Description
This is the same bug class as the `LlamaPolicy.revokePolicy` report: an irreversible, "final" state-clearing operation (burn / delete) is triggered by a check that only looks at a coarse signal (balance == 0 / code hash missing) without accounting for outstanding sub-state that other parties still depend on (remaining roles / other holders' balances), leaving the system in an inconsistent, unrecoverable state.

Concretely:
1. A user deploys and registers an arbitrary ERC20 contract as a native token pair via `RegisterERC20`/`registerERC20` [5](#0-4) . The contract is fully attacker-controlled and can implement any logic, including `selfdestruct`.
2. Other users mint/hold ERC20 balances of this token directly (native ERC20 balances live in the contract's own EVM storage), without necessarily having converted them to the Cosmos-coin representation yet.
3. The attacker calls `selfdestruct` on the contract. `x/vm` keeper's `DeleteAccount` clears the contract's balance, storage, and code hash [2](#0-1) . Because ERC20 balances (the `mapping(address => uint256)` storage slots) belong to the contract account, **all users' unconverted token balances are erased in one unprivileged action**, not just the attacker's own.
4. When anyone subsequently calls `ConvertERC20` or `ConvertCoin` for that pair, the keeper sees `acc == nil || !acc.HasCodeHash()` and calls `k.DeleteTokenPair(ctx, pair)`, deleting the token pair, ERC20 map, denom map and allowances entirely, returning `nil, nil` (success) [3](#0-2) [6](#0-5) . There is no check for whether outstanding (non-module) ERC20 balances existed, no attempt to snapshot/refund holders, and no way to reverse the deletion.
5. The IBC callback path (`ConvertCoinToERC20FromPacket`) exhibits the exact same guard-without-reconciliation pattern for the self-destructed case, silently no-oping and emitting only a best-effort event [7](#0-6) , and integration tests explicitly document this "self-destructed contract" scenario as an expected failure/no-op case rather than a case that protects users [8](#0-7) .

This mirrors the report's core defect: an operation meant to be a graceful cleanup (burn token / delete pair) is performed based on an incomplete precondition check (balance==0 for one actor / code-hash-missing for the contract), permanently discarding value that other, unrelated parties still held an active claim to (remaining roles / unconverted ERC20 balances).

### Impact Explanation
This falls under the Critical "permanent freezing, locking, theft, or unauthorized extraction of user funds ... or token-pair-backed balances" bucket. A single unprivileged actor who deploys and registers a native ERC20 pair (or who acquires/compromises control of one already registered, e.g. via a contract with an admin-controlled self-destruct) can permanently and irrecoverably destroy the ERC20 balances of every other holder of that token who has not yet converted to the Cosmos coin representation, and additionally cause the token pair's on-chain bookkeeping (denom map, ERC20 map, allowances) to be silently deleted. Holders lose access to their tokens with no possible recovery path, since the underlying EVM storage backing those balances no longer exists and the token pair itself is removed.

### Likelihood Explanation
Likelihood depends on whether `PermissionlessRegistration` is enabled or whether governance approves registration of arbitrary/attacker-authored contracts, and whether the registered contract exposes a self-destruct path reachable by an ordinary (non-privileged) transaction. I could not fully verify from the indexed code whether `RegisterERC20`/`CreateCoinMetadata` enforces that the registered bytecode matches a known-safe (audited) ERC20 template (e.g., bytecode hash pinning) before accepting the pair — the search only surfaced the metadata/query validation path, not a bytecode allow-list check. If no such restriction exists, the likelihood is high under permissionless registration; if governance vets contracts and requires a known template, likelihood is lower but still nonzero since standard OpenZeppelin-based ERC20 tokens are not self-destructible, but the `x/erc20` code does not appear to reject non-standard/self-destructible contracts at registration time.

### Recommendation
- Before deleting a token pair on contract self-destruct/missing code hash, snapshot and forcibly migrate remaining, non-module ERC20 balances (or the accounted total supply) into the Cosmos-coin side so holders retain value, rather than unconditionally calling `DeleteTokenPair`.
- Alternatively, prevent registration of self-destructible contracts, or require an escrow/migration window before a token pair can be deleted following a detected self-destruct, during which holders can claim a 1:1 native-coin replacement based on a balance snapshot taken at registration/last-known-state time.
- Emit a clear, queryable "pair frozen pending manual resolution" state instead of an immediate, irreversible `DeleteTokenPair`, and gate final deletion behind a check that no outstanding non-module ERC20 balance existed (or reconcile it first), analogous to splitting `revokePolicy` into role-revocation and a final burn step only once all sub-state has been resolved.

### Proof of Concept
1. Set `x/erc20` params with `PermissionlessRegistration = true` (or get a governance-approved registration of an attacker-authored contract).
2. Deploy a custom ERC20 contract that implements the standard interface plus a public `kill()` function calling `selfdestruct(payable(msg.sender))`.
3. Register the contract via `MsgRegisterERC20` (`x/erc20/keeper/msg_server.go` `RegisterERC20` / `registerERC20`) [1](#0-0) .
4. Mint tokens to Victim A and Victim B directly on the ERC20 contract (both hold balances in contract storage, neither has converted to Cosmos coin yet).
5. Attacker calls `kill()`, triggering EVM `SELFDESTRUCT`, which the `x/vm` keeper handles via `DeleteAccount`, wiping contract storage/balance/code hash [2](#0-1) .
6. Victim A submits `MsgConvertERC20` for their (now-erased) balance; `ConvertERC20` detects `!acc.HasCodeHash()`, calls `DeleteTokenPair`, and returns success with no coins minted — Victim A's tokens are gone and the pair no longer exists for anyone [3](#0-2) [6](#0-5) .
7. Victim B, who never got to submit a conversion, permanently loses their entire ERC20 balance with no remaining code path to recover it.

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

**File:** x/erc20/keeper/proposals.go (L16-41)
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

**File:** x/erc20/keeper/ibc_callbacks.go (L183-253)
```go
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
}

// ConvertCoinToERC20FromPacket converts the IBC coin to ERC20 after refunding the sender
// This function is only executed when IBC timeout or an Error ACK happens.
func (k Keeper) ConvertCoinToERC20FromPacket(ctx sdk.Context, data transfertypes.FungibleTokenPacketData) error {
	// Sender is local (source) chain address; accept local bech32 or 0x-hex
	senderBz, err := k.addrCodec.StringToBytes(data.Sender)
	if err != nil {
		return err
	}
	sender := sdk.AccAddress(senderBz)

	pairID := k.GetTokenPairID(ctx, data.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	if !found {
		// no-op, token pair is not registered
		return nil
	}

	coin := ibc.GetSentCoin(data.Denom, data.Amount)

	switch {

	// Case 1. if pair is native coin -> no-op
	case pair.IsNativeCoin():
		// no-op, received coin is a  native coin
		return nil

	// Case 2. if pair is native ERC20 -> unescrow
	case pair.IsNativeERC20():
		// use a zero gas config to avoid extra costs for the relayers
		ctx = ctx.
			WithKVGasConfig(storetypes.GasConfig{}).
			WithTransientKVGasConfig(storetypes.GasConfig{})

		params := k.GetParams(ctx)
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}

		// assume that all module accounts on Cosmos EVM need to have their tokens in the
		// IBC representation as opposed to ERC20
		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
		if types.IsModuleAccount(senderAcc) {
			return nil
		}

		// Convert from Coin to ERC20
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
			// We want to record only the failed attempt to reconvert the coins during IBC.
			defer func() {
				telemetry.IncrCounter(1, types.ModuleName, "ibc", "error", "total")
			}()
			ctx.EventManager().EmitEvents(
				sdk.Events{
					sdk.NewEvent(
						types.EventTypeFailedConvertERC20,
						sdk.NewAttribute(types.AttributeCoinSourceChannel, pair.Denom),
						sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
						sdk.NewAttribute("error", err.Error()),
					),
				},
			)
			return nil
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
