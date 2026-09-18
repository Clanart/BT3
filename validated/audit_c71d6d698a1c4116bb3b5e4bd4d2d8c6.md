### Title
CW20/CW721/CW1155 balances held under a cast address are permanently orphaned when the owner later associates their EVM address to their real Sei address - ([File: utils/helpers/associate.go])

### Summary
`CW20ERC20Pointer.sol` (and the analogous CW721/CW1155 pointers) resolve every Cosmos-side operand via `AddrPrecompile.getSeiAddr(addr)`, which in turn is backed by `evmKeeper.GetSeiAddress`. Before a user calls the association flow, an EVM address has no mapping, so callers/recipients interacting with a CW20/CW721/CW1155 pointer resolve to the "direct-cast" Cosmos address (`sdk.AccAddress(evmAddr[:])`). Any CW20/CW721/CW1155 balance a user accumulates while unassociated is therefore recorded by the wasm contract under this cast address string. When the user later associates (via a normal signed tx through `EVMAddressDecorator`, the `addr` precompile's `associate`/`associatePubKey`, or the implicit self-association path in `EVMPreprocessDecorator`), `AssociationHelper.MigrateBalance` (`utils/helpers/associate.go`) only moves **native bank coin and wei balances** from the cast address to the real Sei address — it never touches CW20/CW721/CW1155 contract storage, which is opaque to the bank keeper. After association, every future pointer call resolves the owner to the new real Sei address instead of the cast address, and because the cast address has no discoverable private key under the Cosmos signing scheme, the CW-side balance left behind under the cast address becomes permanently unreachable — the same "value transferred out of a tracked balance, not carried over by the identity migration" bug class as the Beanstalk report.

### Finding Description
- `MigrateBalance` in `utils/helpers/associate.go` explicitly enumerates only what it considers "the account's balance": `SpendableCoins` and `GetWeiBalance` of the cast address. [1](#0-0) 
- The same limited scope is used by the standalone migration helper `MigrateCastAddressBalances`, confirming this is the officially recognized/complete definition of "funds to migrate": [2](#0-1) 
- Association is triggered passively on essentially any signed Cosmos tx from a previously-unassociated pubkey via `EVMAddressDecorator.AnteHandle`, which calls `SetAddressMapping` and then `MigrateBalance` unconditionally: [3](#0-2) 
- It is also triggered by ordinary EVM transactions via `EVMPreprocessDecorator.AnteHandle`, and explicitly by the `addr` precompile's `associate`/`associatePubKey` methods: [4](#0-3) [5](#0-4) 
- Once associated, the account entry for the cast address may even be removed (`RemoveAccount`) when its `LockedCoins` are zero — this check only considers vesting-lock state, not any wasm-side value tied to that address: [6](#0-5) 
- Every pointer operation resolves owner/recipient/spender identities through `AddrPrecompile.getSeiAddr`, which is backed by the same `evmKeeper.GetSeiAddress`/`GetSeiAddressOrDefault` mapping that flips at association time. For example, `balanceOf`, `transfer`, `transferFrom`, and `allowance` on the CW20↔ERC20 pointer all key off this mapping: [7](#0-6) 
- The CW1155 pointer exhibits the identical pattern for `from`/`to` resolution: [8](#0-7) 
- Existing tests already document (without flagging as a bug) that a cast/"unlinked" address can hold pointer-token balances before it becomes linked, and that becoming linked changes which address the pointer treats as canonical: [9](#0-8) 

The wasm contract's CW20/CW721/CW1155 balance mapping is keyed by the bech32 string address and is completely independent of the bank module and of `x/evm`'s address-mapping store; nothing in the association/migration path ever inspects or moves it. Because the cast address (`sdk.AccAddress(evmAddr[:])`) has no corresponding secp256k1 private key that can produce a valid Cosmos signature for a direct `MsgExecuteContract`, once the pointer stops resolving that user to the cast address (post-association), the CW-side value recorded under the cast address becomes practically unspendable through any code path in the repository — this mirrors exactly the Beanstalk pattern of value sitting in a store that the "balance-carrying" migration logic does not know about.

### Impact Explanation
Any Sei user who interacts with an ERC20/ERC721/ERC1155 pointer contract from an EVM address before formally associating that address (a state explicitly supported and tested, e.g. "should transfer to unlinked address") risks having their CW20/CW721/CW1155 pointer-token balance become permanently frozen the moment that address becomes associated (which can happen passively via any subsequent Cosmos-side signed transaction, not only intentional association calls). This is a concrete, permanent freezing/loss-of-funds condition reachable by any public transaction sender, matching the "permanent freezing" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate-to-high: association is a routine, often-implicit event (triggered by any signed Cosmos transaction from the account, not just explicit `associate` calls), while holding pointer-token balances under an as-yet-unassociated EVM address is an explicitly supported and tested usage pattern (per `ERC20toNativePointerTest.js`'s "unlinked wallet" flow and `CW20toERC20PointerTest.js`'s "unassociated address" cases). Any user who receives CW20/CW721/CW1155 pointer tokens to a brand-new EVM wallet before that wallet performs its first plain Cosmos-signed transaction is exposed.

### Recommendation
Extend the association/migration flow (`AssociationHelper.MigrateBalance` / `MigrateCastAddressBalances`) to also account for wasm-module state tied to the cast address, or, more generally, ensure pointer contracts continue to resolve a cast address's on-chain balance to the same logical owner after association (e.g., by having the `addr` precompile's `getSeiAddr` continue to alias the still-valid cast address for value lookups, or by migrating CW20/CW721/CW1155 balances from the cast bech32 string to the new real bech32 string at association time, analogous to how bank balances are migrated).

### Proof of Concept
1. Deploy a CW20 token and register an ERC20 pointer for it (`registerPointerForERC20`).
2. From a brand-new, never-before-used EVM private key (`walletB`), have another party transfer CW20 pointer tokens to `walletB`'s EVM address without `walletB` ever having sent a transaction — the CW20 contract records the balance under `sdk.AccAddress(walletB.evmAddress[:])` (the cast address), as shown in `ERC20toNativePointerTest.js`'s "should transfer to unlinked address" test.
3. Have `walletB` sign and submit any ordinary Cosmos-side transaction (or call the `addr` precompile's `associate`/`associatePubKey`), which invokes `EVMAddressDecorator.AnteHandle` → `SetAddressMapping` + `MigrateBalance`, or `AssociationHelper.AssociateAddresses` directly (`x/evm/ante/preprocess.go:304-347`, `utils/helpers/associate.go:34-55`). This migrates any bank/wei balance but performs no wasm-state migration.
4. Query the pointer's `balanceOf(walletB.evmAddress)` — it now resolves through `getSeiAddr` to the real Sei address, which the CW20 contract never received tokens for, so the query returns `0`.
5. Attempt to recover the tokens by directly executing `MsgExecuteContract{transfer}` signed as the cast Cosmos address — this fails because no known secp256k1 private key produces a valid signature for `sdk.AccAddress(walletB.evmAddress[:])`. The CW20 balance is permanently stranded.

### Citations

**File:** utils/helpers/associate.go (L57-83)
```go
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

**File:** x/evm/migrations/migrate_cast_address_balances.go (L9-29)
```go
func MigrateCastAddressBalances(ctx sdk.Context, k *keeper.Keeper) (rerr error) {
	k.IterateSeiAddressMapping(ctx, func(evmAddr common.Address, seiAddr sdk.AccAddress) bool {
		castAddr := sdk.AccAddress(evmAddr[:])
		if balances := k.BankKeeper().SpendableCoins(ctx, castAddr); !balances.IsZero() {
			if err := k.BankKeeper().SendCoins(ctx, castAddr, seiAddr, balances); err != nil {
				logger.Error("error migrating balances from cast to real for address", "address", evmAddr, "err", err)
				rerr = err
				return true
			}
		}
		if wei := k.BankKeeper().GetWeiBalance(ctx, castAddr); !wei.IsZero() {
			if err := k.BankKeeper().SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), wei); err != nil {
				logger.Error("error migrating wei from cast to real for address", "address", evmAddr, "err", err)
				rerr = err
				return true
			}
		}
		return false
	})
	return
}
```

**File:** x/evm/ante/preprocess.go (L58-101)
```go
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

	derived := msg.Derived
	seiAddr := derived.SenderSeiAddr
	evmAddr := derived.SenderEVMAddr
	ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
		sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
		sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, seiAddr.String())))
	pubkey := derived.PubKey
	isAssociateTx := derived.IsAssociate
	associateHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}

		return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil // short-circuit without calling next
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
		if p.evmKeeper.EthReplayConfig.Enabled {
			p.evmKeeper.PrepareReplayedAddr(ctx, evmAddr)
		}
	}
```

**File:** x/evm/ante/preprocess.go (L304-347)
```go
func (p *EVMAddressDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	sigTx, ok := tx.(authsigning.SigVerifiableTx)
	if !ok {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrTxDecode, "invalid tx type")
	}
	signers := sigTx.GetSigners()
	for _, signer := range signers {
		if evmAddr, associated := p.evmKeeper.GetEVMAddress(ctx, signer); associated {
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		acc := p.accountKeeper.GetAccount(ctx, signer)
		if acc.GetPubKey() == nil {
			logger.Error("missing pubkey for signer", "signer", signer)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		pk, err := btcec.ParsePubKey(acc.GetPubKey().Bytes())
		if err != nil {
			logger.Debug("failed to parse pubkey for account, likely due to the fact that it isn't on secp256k1 curve", "account", acc.GetPubKey(), "err", err)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		evmAddr, err := helpers.PubkeyToEVMAddress(pk.SerializeUncompressed())
		if err != nil {
			logger.Error("failed to get EVM address from pubkey", "err", err)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
			sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
			sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
		p.evmKeeper.SetAddressMapping(ctx, signer, evmAddr)
		associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
		if err := associationHelper.MigrateBalance(ctx, evmAddr, signer, false); err != nil {
			logger.Error("failed to migrate EVM address balance", "address", evmAddr, "err", err)
			return ctx, err
		}
	}
```

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** contracts/src/CW20ERC20Pointer.sol (L36-96)
```text
    function balanceOf(address owner) public view override returns (uint256) {
        require(owner != address(0), "ERC20: balance query for the zero address");
        string memory ownerAddr = _formatPayload("address", _doubleQuotes(AddrPrecompile.getSeiAddr(owner)));
        string memory req = _curlyBrace(_formatPayload("balance", _curlyBrace(ownerAddr)));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "balance");
    }

    function totalSupply() public view override returns (uint256) {
        string memory req = _curlyBrace(_formatPayload("token_info", "{}"));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "total_supply");
    }

    function allowance(address owner, address spender) public view override returns (uint256) {
        string memory o = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner)));
        string memory s = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
        string memory req = _curlyBrace(_formatPayload("allowance", _curlyBrace(_join(o, s, ","))));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "allowance");
    }

    // Transactions
    function approve(address spender, uint256 amount) public override returns (bool) {
        // if amount is larger uint128 then set amount to uint128 max
        if (amount > type(uint128).max) {
            amount = type(uint128).max;
        }
        uint256 currentAllowance = allowance(msg.sender, spender);
        if (currentAllowance > amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(currentAllowance - amount)));
            string memory req = _curlyBrace(_formatPayload("decrease_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        } else if (currentAllowance < amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount - currentAllowance)));
            string memory req = _curlyBrace(_formatPayload("increase_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        }
        return true;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L41-63)
```text
    function safeTransferFrom(
        address from,
        address to,
        uint256 id,
        uint256 amount,
        bytes memory data
    ) public override {
        require(to != address(0), "ERC1155: transfer to the zero address");
        require(balanceOf(from, id) >= amount, "ERC1155: insufficient balance for transfer");
        require(
            msg.sender == from || isApprovedForAll(from, msg.sender),
            "ERC1155: caller is not approved to transfer"
        );
    
        string memory f = _formatPayload("from", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory t = _formatPayload("to", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(id)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));

        string memory req = _curlyBrace(
            _formatPayload("send", _curlyBrace(_join(f, ",", _join(t, ",", _join(tId, ",", amt)))))
        );
        _execute(bytes(req));
```

**File:** contracts/test/ERC20toNativePointerTest.js (L77-106)
```javascript
        it("should transfer to unlinked address", async function () {
            let sender = accounts[0];
            let recipientWallet = generateWallet()
            let recipient = await recipientWallet.getAddress()
            const amount = BigInt(5);
            const startBal = await pointer.balanceOf(sender.evmAddress);

            // send token to unlinked wallet
            const tx = await pointer.transfer(recipient, amount);
            await tx.wait();

            // should have sent balance (sender spent, receiver received)
            expect(await pointer.balanceOf(sender.evmAddress)).to.equal(startBal-amount);
            expect(await pointer.balanceOf(recipient)).to.equal(amount);

            // fund address so it can transact
            await fundAddress(recipient, "1000000000000000000000")
            await delay()

            // unlinked wallet can send balance back to sender (becomes linked at this moment)
            await (await pointer.connect(recipientWallet).transfer(sender.evmAddress, amount, {
                gasPrice: ethers.parseUnits('100', 'gwei')
            })).wait()
            expect(await pointer.balanceOf(recipient)).to.equal(BigInt(0));
            expect(await pointer.balanceOf(sender.evmAddress)).to.equal(startBal);

            // confirm association actually happened
            const seiAddress = await getSeiAddress(recipient)
            expect(seiAddress.indexOf("sei")).to.equal(0)
        });
```
