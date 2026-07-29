Based on my research, I found a genuine structural analog to the reported bug class in `x/erc20`, though I was unable to fully trace CREATE2 redeployment behavior in `x/vm` before running out of tool budget — I flag that uncertainty explicitly in the Likelihood section.

### Title
Irreversible TokenPair deletion triggered by a transient/attacker-observable code-absence signal, permanently orphaning escrowed backing value - (File: x/erc20/keeper/msg_server.go)

### Summary
The oracle bug's root cause was using an externally-influenceable, transient balance observation (zero balance at end of period) as a proxy for a one-way, cumulative state fact ("validator has withdrawn"), letting an unprivileged actor corrupt a monotonic accounting counter. `x/erc20`'s `ConvertERC20` and `ConvertCoin` handlers use the same class of proxy signal — `acc == nil || !acc.HasCodeHash()` — to make an irreversible decision (`DeleteTokenPair`) about a token pair's validity, triggered by any unprivileged caller who happens to invoke conversion while the contract account transiently has no code.

### Finding Description
In `ConvertERC20`: [1](#0-0) 

and in `ConvertCoin`: [2](#0-1) 

both check `k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())` and, if the account is nil or lacks a code hash, call `k.DeleteTokenPair`, which permanently removes the ERC20↔denom mapping and wipes allowances: [3](#0-2) 

This deletion is triggered by **any unprivileged caller** simply invoking `MsgConvertERC20`/`MsgConvertCoin` — it requires no special permission and is a normal, expected code path (the code comments confirm it is meant to reap token pairs whose ERC20 contract self-destructed). The design assumes "no code at this address" is a permanent, terminal fact about the token pair. This mirrors the oracle bug's flawed assumption that "balance is currently zero" is a permanent, terminal fact about a validator.

The same reasoning is echoed in `ConvertCoinToERC20FromPacket`, used during IBC ack/timeout handling, and in `OnAcknowledgementPacket`/`OnTimeoutPacket` doc comments referencing "self-destructed ERC20 contract" as a recognized, expected failure mode: [4](#0-3) 

Once `DeleteTokenPair` runs, native coins already minted/escrowed under `pair.Denom` (from prior legitimate `ConvertERC20` calls, held as bank balances by users, or as ERC20 tokens escrowed in `types.ModuleAddress`) lose their conversion path back to ERC20 permanently — there is no code path to re-associate the same denom with a token pair through this reaping logic, since it is a delete-only operation.

### Impact Explanation
If a registered native-ERC20 contract enters a state where the account momentarily reports no code hash (self-destruct followed by any subsequent redeploy at the same address, or any other event that makes `HasCodeHash()` return false without the destruction actually being final/governance-reviewed), an ordinary user calling `ConvertERC20`/`ConvertCoin` — which requires no privilege — irreversibly deletes the token pair mapping and its allowances. This permanently strips the associated coin denom of its ability to convert back into the ERC20 representation, effectively freezing/orphaning the value backing that token pair (escrowed ERC20 balance in `types.ModuleAddress` and any coin holders' redemption rights), matching the in-scope "Critical permanent freezing... of token-pair-backed balances" impact.

### Likelihood Explanation
I was not able to fully verify, within the available tool budget, whether `x/vm`'s state transition logic permits a contract to be functionally redeployed at the same address after `DeleteAccount`/self-destruct within this codebase (e.g., via CREATE2 in a later transaction), which would be the most direct unprivileged trigger for a momentary `HasCodeHash() == false` window followed by contract resurrection. `DeleteAccount` does fully remove code hash, code, storage, and the auth account on self-destruct: [5](#0-4) 
which is consistent with EVM semantics that would allow redeployment. Independent of redeployment, the more certain and immediately reachable trigger is: any legitimate/expected self-destruct of a native-ERC20 contract, followed by an ordinary (non-privileged) user calling `ConvertERC20` or `ConvertCoin`, which the code's own comments treat as an expected, common occurrence — meaning the "freezing of already-escrowed value" side effect is a direct, low-effort, and already partially acknowledged consequence of this reaping design, not a purely theoretical corner case.

### Recommendation
Do not use `HasCodeHash()`/account-existence as the sole, unprivileged-triggerable signal to irreversibly delete a token pair and wipe allowances. Instead, require this cleanup to be gated by a privileged/governance path, or ensure any escrowed backing coin/ERC20 balance is fully and atomically settled (refunded/burned/redeemed 1:1) as part of the same deletion transaction so that no `pair.Denom` balance can outlive its ability to be redeemed. At minimum, verify that a redeployed contract at the same address cannot silently take over a stale `pair.Denom`/allowance mapping, and add explicit invariant checks tying total escrowed value to the existence of a valid, non-deleted token pair before allowing pair deletion.

### Proof of Concept
1. Governance (or permissionless registration) registers a native ERC20 contract `C` as a token pair with denom `D`.
2. Users call `ConvertERC20` multiple times, escrowing ERC20 balance in `types.ModuleAddress` and minting/holding native coin `D`.
3. Contract `C` self-destructs (a legitimate, expected event per the code's own failure-handling comments).
4. Any unprivileged user calls `ConvertCoin` (or `ConvertERC20`) referencing pair `C`/`D`. The `acc.HasCodeHash()` check fails, and `DeleteTokenPair` is executed, deleting the pair and allowances.
5. All outstanding `D` balances held by other users are now permanently unable to reconvert to ERC20 form via this mechanism, and the escrowed ERC20 balance in `types.ModuleAddress` becomes orphaned with no governance-reviewed remediation path in this code — a permanent freezing of user-held, token-pair-backed value triggered by an ordinary, unprivileged transaction.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L180-188)
```go
// OnTimeoutPacket converts the IBC coin to ERC20 after refunding the sender
// since the original packet sent was never received and has been timed out.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
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
