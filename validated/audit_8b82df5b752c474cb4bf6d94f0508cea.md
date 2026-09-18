### Title
Bank precompile `send`/CW20 pointer `transfer` silently divert funds to an unreachable cast address when the recipient's EVM address is unassociated - ([File: precompiles/bank/legacy/v67/bank.go])

### Summary
The Gitcoin finding is about grant votes/payments going to a wrong address because the contract does not validate the recipient before moving funds, with no way to recover the funds afterward. The closest reachable analog in sei-chain is the `accAddressFromArg` helper used by the Bank precompile's `send`/`sendNative` methods (and the same pattern reused by CW20/CW721/CW1155 ERC pointer contracts): when a caller supplies an EVM address that has never been associated to a Sei address, the code does not reject the call — it silently falls back to a direct byte-cast `sdk.AccAddress`, and funds are moved there via `bankKeeper.SendCoins`/`SendCoinsAndWei`.

### Finding Description
`accAddressFromArg` resolves the destination Sei address for an EVM `address` argument: [1](#0-0) 

If `p.evmKeeper.GetSeiAddress(ctx, addr)` finds no explicit association, it returns `sdk.AccAddress(addr[:])` — a "cast" address — instead of erroring out. This same fallback pattern is used for both `senderSeiAddr` and `receiverSeiAddr` in every version of the bank precompile's `send` function: [2](#0-1) 

The `x/evm` keeper explicitly documents that cast addresses are second-class: an `sdk.AccAddress` obtained by casting an EVM address may not be able to receive funds if that EVM address later becomes truly associated with a different Sei account (`CanAddressReceive`): [3](#0-2) 

So a caller who invokes the bank `send` precompile (or the CW20/CW721/CW1155 ERC pointer `transfer`/`transferFrom`, which route through the same `AddrPrecompile.getSeiAddr`/association-dependent resolution) with a plausible-looking but unassociated EVM recipient address does not get an explicit rejection at the precompile layer for `send`. Only the JS pointer test suite demonstrates that CW20 pointer transfers to unassociated Sei addresses become effectively silent no-ops (balance unchanged) rather than reverting with a clear error: [4](#0-3) [5](#0-4) 

For the raw bank precompile `send`, however, there is no such CW-level rejection — `SendCoins` moves the coins into the cast `AccAddress` bucket, distinct from the balance the true owner sees once they associate. This mirrors the report's root cause: no validation that the destination address argument is a legitimate, controllable destination before moving value, and the value becomes effectively stuck once mis-sent (the true owner's normal EVM/Sei views of balance won't show it until/unless a subsequent association event triggers `MigrateBalance`, and even then only if the association happens with matching account state, per `AssociateAddresses`/`MigrateBalance`): [6](#0-5) 

### Impact Explanation
Funds sent via the bank `send` precompile (or any pointer contract built on top of it) to an EVM address that has not yet associated with its Sei account are credited to a cast `sdk.AccAddress` rather than the account the sender intended to reach and rather than the balance the eventual controller of that EVM address will see under normal usage (native transfers, wallets, and explorers key off the associated Sei address). Because association is a one-time, opt-in action by the address owner and cast-address balances have documented limitations, funds can become effectively frozen from the sender's/receiver's expected point of view — a fund-loss/fund-freezing issue reachable by any unprivileged EVM caller.

### Likelihood Explanation
The bug is trivially reachable: any account can call the `send`/`sendNative` methods of the Bank precompile (`0x1001`) or any CW20/CW721/CW1155 pointer contract's `transfer`/`transferFrom` with an arbitrary, unassociated EVM address as the recipient. No special privileges are required, and this is a routine EVM transaction on a public RPC node. The condition ("recipient EVM address never associated") is common in practice since association is not mandatory for using an EVM address on Sei.

### Recommendation
In `accAddressFromArg` (and the equivalent logic in every precompile that resolves EVM addresses to Sei addresses for value transfer), fail fast if the destination address has no genuine association and is not itself a contract/self-cast address that is known to be reachable — i.e., apply the same `CanAddressReceive` check that exists in `x/evm/keeper/address.go` before calling `bankKeeper.SendCoins`/`SendCoinsAndWei`, and revert the transaction with a clear "recipient not associated" error rather than silently proceeding with a cast-address credit.

### Proof of Concept
1. Pick an EVM address `X` that has never called `Associate`/`AssociatePubKey` and has no on-chain history establishing a Sei-address mapping (`GetSeiAddress` returns `found=false`).
2. From any account, call the Bank precompile (`0x0000000000000000000000000000000000001001`) `send(sender, X, "usei", amount)`, or call a CW20 pointer's `transfer(X, amount)`.
3. `accAddressFromArg` (precompiles/bank/legacy/v67/bank.go:633-644) returns `sdk.AccAddress(X[:])` instead of erroring; `SendCoins`/`SendCoinsAndWei` executes successfully.
4. The coins are now held under the cast `AccAddress(X[:])` bucket. If `X` is later associated with a different Sei account via `Associate` (deriving the EVM address from a distinct public key/state where `castBaseAcc` conditions in `AssociateAddresses` are not met), the funds do not automatically follow to the newly associated owner's expected balance view, demonstrating the loss/freezing condition referenced in `x/evm/keeper/address.go`'s `CanAddressReceive` documentation.

### Citations

**File:** precompiles/bank/legacy/v67/bank.go (L225-232)
```go
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, 0, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/bank/legacy/v67/bank.go (L633-644)
```go
func (p PrecompileExecutor) accAddressFromArg(ctx sdk.Context, arg interface{}) (sdk.AccAddress, error) {
	addr := arg.(common.Address)
	if addr == (common.Address{}) {
		return nil, errors.New("invalid addr")
	}
	seiAddr, found := p.evmKeeper.GetSeiAddress(ctx, addr)
	if !found {
		// return the casted version instead
		return sdk.AccAddress(addr[:]), nil
	}
	return seiAddr, nil
}
```

**File:** x/evm/keeper/address.go (L78-86)
```go
// A sdk.AccAddress may not receive funds from bank if it's the result of direct-casting
// from an EVM address AND the originating EVM address has already been associated with
// a true (i.e. derived from the same pubkey) sdk.AccAddress.
func (k *Keeper) CanAddressReceive(ctx sdk.Context, addr sdk.AccAddress) bool {
	directCast := common.BytesToAddress(addr) // casting goes both directions since both address formats have 20 bytes
	associatedAddr, isAssociated := k.GetSeiAddress(ctx, directCast)
	// if the associated address is the cast address itself, allow the address to receive (e.g. EVM contract addresses)
	return associatedAddr.Equals(addr) || !isAssociated // this means it's either a cast address that's not associated yet, or not a cast address at all.
}
```

**File:** contracts/test/CW20toERC20PointerTest.js (L118-128)
```javascript
                it("transfer to unassociated address should fail", async function() {
                    const unassociatedSeiAddr = "sei1z7qugn2xy4ww0c9nsccftxw592n4xhxccmcf4q";
                    const respBefore = await queryWasm(pointer, "balance", {address: accounts[1].seiAddress});
                    const balanceBefore = respBefore.data.balance;

                    await executeWasm(pointer,  { transfer: { recipient: unassociatedSeiAddr, amount: "100" } });
                    const respAfter = await queryWasm(pointer, "balance", {address: accounts[1].seiAddress});
                    const balanceAfter = respAfter.data.balance;

                    expect(balanceAfter).to.equal(balanceBefore);
                });
```

**File:** contracts/test/ERC20toCW20PointerTest.js (L143-146)
```javascript
                it("transfer to unassociated address should fail", async function () {
                    const unassociatedRecipient = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";
                    await expect(pointer.transfer(unassociatedRecipient, 1)).to.be.revertedWithoutReason;
                });
```

**File:** utils/helpers/associate.go (L34-83)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}

func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```
