This confirms the analog. `BuildContractAddress(codeID, instanceID)` derives the future contract address purely from `codeID` and a global auto-incrementing `instanceID` counter, both fully predictable to any observer watching the chain's current `KeyLastInstanceID` sequence value.### Title
Denial-of-service on CosmWasm `instantiate` via pre-created BaseAccount at predictable contract address - ([File: sei-wasmd/x/wasm/keeper/keeper.go])

### Summary
The CosmWasm contract-instantiation path derives the future contract's account address purely from the code ID and a chain-global auto-incrementing instance counter, both of which are fully predictable off-chain before the instantiate transaction lands. Because `x/bank`'s `SendCoins`/`InputOutputCoins` automatically create a `BaseAccount` for *any* recipient address without requiring that address's permission, an attacker can pre-create an account at the exact address the next `MsgInstantiateContract` will target. This causes the legitimate instantiation to abort with `ErrAccountExists`, exactly analogous to the Solana `off_ramp_authority::initialize` bug where an attacker front-runs `init` of an associated-token-account PDA by pre-creating it.

### Finding Description
`generateContractAddress` computes the contract's account address deterministically: [1](#0-0) 

`instanceID` comes from a globally shared, monotonically-incrementing sequence (`KeyLastInstanceID`) that is readable by anyone via genesis/state queries and trivially predictable by watching chain activity (it increments by exactly 1 per contract instantiation network-wide, across all code IDs): [2](#0-1) 

`instantiate` then checks whether an account already exists at that address and aborts the entire instantiate transaction if so: [3](#0-2) 

Meanwhile, `x/bank`'s `SendCoins` (reachable from any unprivileged `MsgSend`/`MsgMultiSend` transaction, or the wasmd/EVM bridges that route to it) auto-creates a `BaseAccount` for the recipient address if none exists yet — with no signature or consent from that address required: [4](#0-3) 

Because contract addresses derived by `BuildContractAddress(codeID, instanceID)` are 20/32-byte account addresses in the same address space as ordinary Sei accounts, an attacker can:
1. Observe the current `KeyLastInstanceID` value (via `PeekAutoIncrementID`/genesis export or by simply tracking public instantiate transactions).
2. Predict the exact next N contract addresses for any code ID using `BuildContractAddress(codeID, instanceID+i)`.
3. Send a minimal-amount `MsgSend` (1 `usei`) to each predicted address before the legitimate `MsgInstantiateContract` executes, causing `BaseAccount`s to be created there.
4. Any subsequent `instantiate` call that would have landed on one of those addresses instead fails with `types.ErrAccountExists` ("contract account already exists").

This is functionally identical to the reported Solana bug class: a deterministically-derivable account address, creatable by an unrelated third party without permission, whose pre-existence causes the legitimate privileged initialization instruction to hard-fail.

### Impact Explanation
Any deployer's `MsgInstantiateContract` transaction (via CLI, SDK, or the wasmd EVM precompile's `instantiate`/`instantiate2` paths) can be permanently front-run and blocked at negligible cost (a few `usei` in fees per griefed address). Because `instanceID` is a single global counter, blocking one address only requires the attacker to keep pace with the current sequence value; a sustained low-cost griefing campaign can block *all* new CosmWasm contract deployments network-wide, indefinitely, unless the sequence is somehow advanced past the attacker's precomputed set (which the attacker can trivially keep ahead of). This is a chain-wide, permanent denial of service on a core CosmWasm capability reachable by any unprivileged transaction sender.

### Likelihood Explanation
High. No privileged access, no validator collusion, and no race-condition timing beyond ordinary same-block/next-block transaction ordering is required — the attacker only needs to submit ordinary `MsgSend` transactions ahead of any observed `MsgInstantiateContract`. The `KeyLastInstanceID` sequence and `codeID` are both public. This mirrors the "cheap for the attacker and can be repeated" characterization from the original report.

### Recommendation
Do not treat "account already exists" as a hard failure when the account is a plain, code-less `BaseAccount` with zero sequence/pubkey that was implicitly created only via a bank transfer (i.e., no `PubKey` set and no prior contract/code association). In `instantiate` (sei-wasmd/x/wasm/keeper/keeper.go), when `existingAcct != nil`, verify it is not merely an auto-vivified empty `BaseAccount`: if it has no public key and zero sequence, allow instantiation to proceed by overwriting/reusing it (analogous to `init_if_needed` semantics) rather than rejecting with `ErrAccountExists`. Alternatively, incorporate an unpredictable/creator-bound component (e.g., creator address, hash of `initMsg`, or a per-creator salt akin to `instantiate2`) into every non-`Instantiate2` contract address derivation so it cannot be precomputed and squatted by a third party.

### Proof of Concept
1. Attacker queries `PeekAutoIncrementID(ctx, types.KeyLastInstanceID)` equivalent state (or simply observes the last `EventTypeInstantiate` on-chain) to learn the current `instanceID = N`.
2. Attacker computes `victimAddr := BuildContractAddress(codeID, N+1)` for the `codeID` the victim is known to be about to instantiate (code IDs are also public once uploaded via `MsgStoreCode`).
3. Attacker submits `MsgSend{From: attacker, To: victimAddr, Amount: 1usei}`, which via `BaseSendKeeper.SendCoins` auto-creates a `BaseAccount` at `victimAddr`.
4. Victim submits `MsgInstantiateContract{CodeID: codeID, ...}`; `instantiate` computes the same `victimAddr` (since it is the next available `instanceID`), finds `existingAcct != nil`, and returns `types.ErrAccountExists`, aborting the transaction.
5. Attacker repeats step 2–3 for `N+2, N+3, ...` to permanently deny all future instantiations.

### Citations

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L298-303)
```go
	// create contract address
	contractAddress := k.generateContractAddress(ctx, codeID)
	existingAcct := k.accountKeeper.GetAccount(ctx, contractAddress)
	if existingAcct != nil {
		return nil, nil, sdkerrors.Wrap(types.ErrAccountExists, existingAcct.GetAddress().String())
	}
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1040-1052)
```go
// generates a contract address from codeID + instanceID
func (k Keeper) generateContractAddress(ctx sdk.Context, codeID uint64) sdk.AccAddress {
	instanceID := k.autoIncrementID(ctx, types.KeyLastInstanceID)
	return BuildContractAddress(codeID, instanceID)
}

// BuildContractAddress builds an sdk account address for a contract.
func BuildContractAddress(codeID, instanceID uint64) sdk.AccAddress {
	contractID := make([]byte, 16)
	binary.BigEndian.PutUint64(contractID[:8], codeID)
	binary.BigEndian.PutUint64(contractID[8:], instanceID)
	return address.Module(types.ModuleName, contractID)[:types.ContractAddrLen]
}
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1054-1064)
```go
func (k Keeper) autoIncrementID(ctx sdk.Context, lastIDKey []byte) uint64 {
	store := ctx.KVStore(k.storeKey)
	bz := store.Get(lastIDKey)
	id := uint64(1)
	if bz != nil {
		id = binary.BigEndian.Uint64(bz)
	}
	bz = sdk.Uint64ToBigEndian(id + 1)
	store.Set(lastIDKey, bz)
	return id
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L162-182)
```go
// SendCoins transfers amt coins from a sending account to a receiving account.
// An error is returned upon failure.
func (k BaseSendKeeper) SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	if err := k.SendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt); err != nil {
		return err
	}

	// Create account if recipient does not exist.
	//
	// NOTE: This should ultimately be removed in favor a more flexible approach
	// such as delegated fee messages.
	accExists := k.ak.HasAccount(ctx, toAddr)
	if !accExists {
		defer func() {
			recordNewAccounts(ctx.Context(), 1)
		}()
		k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, toAddr))
	}

	return nil
}
```
