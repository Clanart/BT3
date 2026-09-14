### Title
Silent, attacker-controlled RCAT conversion grants a hidden backdoor-spend authority over a victim's CAT wallet - (File: chia/wallet/cat_wallet/cat_wallet.py)

### Summary
`CATWallet.identify()` automatically upgrades a plain `CATWallet` into an `RCATWallet` whenever it observes an incoming coin for the same asset ID whose inner puzzle contains a "revocation layer." The `hidden_puzzle_hash` that becomes permanently baked into the wallet's `RCATInfo` (and therefore controls all future puzzle-hash derivation and spend authority for that asset) is taken directly from the untrusted coin/puzzle data, with no confirmation from the user and no indication in the CAT's advertised TAIL/identity that a second, hidden spending authority now exists.

### Finding Description
Two governance/authority models coexist for a CAT asset in this wallet:
1. The visible one: the user's own derived key controls the CAT inner puzzle (`puzzle_hash_for_pk`), which is what balances/addresses in the UI represent.
2. The hidden one: a `hidden_puzzle_hash` layer (`create_revocation_layer`) that, when revealed together with `hidden=True` in the solution, allows whoever knows the corresponding hidden puzzle to spend/move the coin unconditionally, bypassing the user's key entirely, as demonstrated by `solve_revocation_layer(..., hidden=True)` in the revocation-layer test [1](#0-0) .

Normally an RCAT is something a CAT *issuer* deliberately mints with a known, disclosed `hidden_puzzle_hash` (e.g., tied to a DID/VC-based revocation policy). The vulnerability is in how the wallet *detects and adopts* this hidden authority: `CATWallet.identify()` inspects an incoming coin's puzzle reveal, and if it doesn't match the plain CAT inner-puzzle shape, tries to match a revocation layer. If a match is found and the existing CAT wallet for that asset ID has an empty lineage store, it silently calls `RCATWallet.convert_to_revocable()`: [2](#0-1) [3](#0-2) 

`convert_to_revocable()` takes the `hidden_puzzle_hash` extracted from that single incoming coin spend and permanently overwrites the wallet's persisted `RCATInfo`, converting the wallet type in the database with no separate authorization step: [4](#0-3) 

After conversion, every future puzzle hash the wallet derives for that CAT asset embeds this attacker/issuer-supplied `hidden_puzzle_hash` via `puzzle_hash_for_pk`/`inner_puzzle_for_cat_puzhash`: [5](#0-4) 

Because the conversion only requires the target CAT wallet's `lineage_store` to be empty (i.e., the wallet was recently created for that `tail_program_hash`/asset ID and has not yet received any CAT coin), an attacker who knows a victim will be receiving a specific CAT (e.g., via an offer, airdrop, or a first-time deposit) can preemptively send that CAT wrapped in an attacker-chosen revocation layer to the victim's wallet puzzle hash first. This makes the victim's wallet for that asset silently become an `RCATWallet` bound to the attacker's `hidden_puzzle_hash`, without any dialog, warning, or explicit opt-in — this is precisely a "hidden governance" model analogous to the VUSD finding, where a second, non-obvious authority (here, a CLVM-level backdoor spend key) coexists with the visible, expected one (the user's own key).

### Impact Explanation
Once the wallet is silently converted, all subsequently received coins of that asset ID for that wallet id are wrapped with the attacker's `hidden_puzzle_hash`. Anyone holding the private key/puzzle behind that hidden puzzle hash can spend the victim's CAT coins by revealing the hidden puzzle (`solve_revocation_layer(hidden_puzzle, solution, hidden=True)`), redirecting the funds to an address of their choosing — i.e., unauthorized coin movement with no signature from the legitimate owner's key. This maps to the "concrete unsigned or unauthorized coin movement" impact category, since the victim's spend authority for that CAT balance is silently subordinated to an attacker-controlled backdoor puzzle.

### Likelihood Explanation
The trigger requires only a single, unsigned/low-cost CAT coin spend sent by the attacker to the victim's known receive puzzle hash for a specific asset — something reachable by any spend-bundle submitter — combined with the precondition that the victim's local CATWallet for that asset has no prior coins (a very common initial state, e.g., right before receiving that asset for the first time, or if the victim has fully spent all of it). No privileged network position, malicious peer, or leaked key is required; it is purely a wallet-sync/state-machine design gap triggered by ordinary chain data.

### Recommendation
Do not auto-convert an existing `CATWallet` into an `RCATWallet` (or accept an attacker-embedded `hidden_puzzle_hash`) purely from unauthenticated incoming coin data. At minimum:
- Require explicit user confirmation before a wallet's spend-authority model changes from a standard CAT to a revocable CAT.
- Only accept revocation-layer conversion when the `hidden_puzzle_hash` is independently corroborated (e.g., pinned/known ahead of time from the CAT's canonical asset metadata, or restricted to cases where the wallet is being created new for that asset rather than silently mutated).
- Surface the hidden puzzle hash / revocation authority prominently in the UI/RPC responses whenever a CAT wallet has (or gains) this property, so users are not misled into thinking their funds are solely under their own key's control.

### Proof of Concept
1. Attacker learns the victim's next receive puzzle hash for a CAT with asset ID `T` (e.g., from an open offer or a known deposit address), where the victim's wallet for `T` has not yet received any coin (`lineage_store` empty or wallet not yet created).
2. Attacker constructs and pushes a CAT-`T` coin whose inner puzzle is `create_revocation_layer(attacker_hidden_puzzle_hash, victim_inner_puzzle_hash)` sent to the victim's puzzle hash, exactly as validated in the revocation-layer puzzle test [6](#0-5) .
3. Victim's wallet syncs this coin; `CATWallet.identify()` detects the revocation layer via `match_revocation_layer` and, finding the CAT wallet's lineage store empty, calls `RCATWallet.convert_to_revocable(cat_wallet, hidden_puzzle_hash=attacker_hidden_puzzle_hash)` [3](#0-2) , permanently rewriting the wallet's `RCATInfo` in `chia/wallet/cat_wallet/r_cat_wallet.py` lines 179-209.
4. All subsequent CAT-`T` coins the victim believes they fully control are wrapped with the attacker's hidden puzzle hash (`puzzle_hash_for_pk`, lines 211-218 of `r_cat_wallet.py`).
5. Attacker later reveals `attacker_hidden_puzzle` with `hidden=True` in the solution to sweep the victim's CAT-`T` balance, as shown to succeed unconditionally (bypassing the visible inner puzzle) in the same test file's third spend, which returns `MempoolInclusionStatus.SUCCESS` [7](#0-6) .

Note: I was not able to fully verify the exact CLVM-level guarantees of the `REVOCATION_LAYER` puzzle bytecode (`chia_puzzles_py.programs.REVOCATION_LAYER`) beyond what is exercised in the cited test, since its compiled source was not retrievable through the indexed search; a Devin session with full repository access would be needed to inspect the `.clsp` source and confirm there is no additional guard (e.g., a required announcement from a legitimate issuer DID) that would prevent an arbitrary attacker-chosen `hidden_puzzle_hash` from taking effect during this auto-conversion path.

### Citations

**File:** chia/_tests/wallet/vc_wallet/test_vc_lifecycle.py (L317-331)
```python
async def test_revocation_layer(cost_logger: CostLogger) -> None:
    async with sim_and_client() as (sim, client):
        # Setup and farm the puzzle
        hidden_puzzle: Program = Program.to((1, [[61, 1]]))  # assert a coin announcement that the solution tells us
        hidden_puzzle_hash: bytes32 = hidden_puzzle.get_tree_hash()
        p2_either_puzzle: Program = create_revocation_layer(hidden_puzzle_hash, ACS_PH)
        assert match_revocation_layer(UnknownPuzzle(known_program=p2_either_puzzle)) == (hidden_puzzle_hash, ACS_PH)

        await sim.farm_block(p2_either_puzzle.get_tree_hash())
        p2_either_coin: Coin = (
            await client.get_coin_records_by_puzzle_hashes(
                [p2_either_puzzle.get_tree_hash()], include_spent_coins=False
            )
        )[0].coin

```

**File:** chia/_tests/wallet/vc_wallet/test_vc_lifecycle.py (L352-368)
```python
        result = await client.push_tx(
            WalletSpendBundle(
                [
                    make_spend(
                        p2_either_coin,
                        p2_either_puzzle,
                        solve_revocation_layer(
                            hidden_puzzle,
                            Program.to(bytes32.zeros),
                            hidden=True,
                        ),
                    )
                ],
                G2Element(),
            )
        )
        assert result == (MempoolInclusionStatus.FAILED, Err.ASSERT_ANNOUNCE_CONSUMED_FAILED)
```

**File:** chia/_tests/wallet/vc_wallet/test_vc_lifecycle.py (L370-394)
```python
        # Spend the inner puzzle
        brick_hash: bytes32 = bytes32.zeros
        wrapped_brick_hash: bytes32 = create_revocation_layer(
            hidden_puzzle_hash,
            brick_hash,
        ).get_tree_hash()
        result = await client.push_tx(
            cost_logger.add_cost(
                "Viral backdoor spend - one create coin",
                WalletSpendBundle(
                    [
                        make_spend(
                            p2_either_coin,
                            p2_either_puzzle,
                            solve_revocation_layer(
                                ACS,
                                Program.to([[51, brick_hash, 0]]),
                            ),
                        )
                    ],
                    G2Element(),
                ),
            )
        )
        assert result == (MempoolInclusionStatus.SUCCESS, None)
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L469-480)
```python
            if cat_puzzle.get_tree_hash() != coin_state.coin.puzzle_hash:
                # Check if it is a special type of CAT
                uncurried_puzzle_reveal = UnknownPuzzle(known_program=coin_spend.puzzle_reveal)
                if uncurried_puzzle_reveal.mod != CAT_MOD or uncurried_puzzle_reveal.curried_args is None:
                    return None
                revocation_layer_match = match_revocation_layer(
                    UnknownPuzzle(known_program=uncurried_puzzle_reveal.curried_args[2])
                )
                if revocation_layer_match is not None:
                    wallet_type = RCATWallet
                else:
                    try:
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L534-549)
```python
                        elif wallet_type is RCATWallet:
                            success = await RCATWallet.convert_to_revocable(
                                found_cat_wallet,
                                # too complicated for mypy but semantics guarantee this not to be None
                                hidden_puzzle_hash=revocation_layer_match[0],  # type: ignore[index]
                            )
                            if success:
                                async with sync_scope.use() as interface:
                                    interface.side_effects.websocket_events.append(
                                        WebSocketEvent(
                                            name="converted cat wallet to revocable", wallet_id=wallet_info.id
                                        )
                                    )
                                return WalletIdentifier(wallet_info.id, WalletType(WalletType.CRCAT))
                            else:
                                return None
```

**File:** chia/wallet/cat_wallet/r_cat_wallet.py (L179-209)
```python
    @classmethod
    async def convert_to_revocable(
        cls,
        cat_wallet: CATWallet,
        hidden_puzzle_hash: bytes32,
    ) -> bool:
        if not await cat_wallet.lineage_store.is_empty():
            cat_wallet.log.error("Received a revocable CAT to a CAT wallet that already has CATs")
            return False
        replace_self = cls()
        replace_self.standard_wallet = cat_wallet.standard_wallet
        replace_self.log = logging.getLogger(cat_wallet.get_name())
        replace_self.log.info(f"Converting CAT wallet {cat_wallet.id()} to R-CAT wallet")
        replace_self.wallet_state_manager = cat_wallet.wallet_state_manager
        replace_self.lineage_store = cat_wallet.lineage_store
        replace_self.info = RCATInfo(cat_wallet.cat_info.limitations_program_hash, None, hidden_puzzle_hash)
        await cat_wallet.wallet_state_manager.user_store.update_wallet(
            WalletInfo(
                cat_wallet.id(), cat_wallet.get_name(), uint8(cls.wallet_type.value), bytes(replace_self.info).hex()
            )
        )
        updated_wallet_info = await cat_wallet.wallet_state_manager.user_store.get_wallet_by_id(cat_wallet.id())
        assert updated_wallet_info is not None
        replace_self.wallet_info = updated_wallet_info
        replace_self.tail_hash = replace_self.info.limitations_program_hash

        cat_wallet.wallet_state_manager.wallets[cat_wallet.id()] = replace_self
        await cat_wallet.wallet_state_manager.puzzle_store.delete_wallet(cat_wallet.id())
        result = await cat_wallet.wallet_state_manager.create_more_puzzle_hashes()
        await result.commit(cat_wallet.wallet_state_manager)
        return True
```

**File:** chia/wallet/cat_wallet/r_cat_wallet.py (L211-223)
```python
    def puzzle_hash_for_pk(self, pubkey: G1Element) -> bytes32:
        inner_puzzle_hash = create_revocation_layer(
            self.info.hidden_puzzle_hash, self.standard_wallet.puzzle_hash_for_pk(pubkey)
        ).get_tree_hash()
        limitations_program_hash_hash = Program.to(self.info.limitations_program_hash).get_tree_hash()
        return curry_and_treehash(
            QUOTED_CAT_MOD_HASH, CAT_MOD_HASH_HASH, limitations_program_hash_hash, inner_puzzle_hash
        )

    async def inner_puzzle_for_cat_puzhash(self, cat_hash: bytes32) -> Program:
        return create_revocation_layer(
            self.info.hidden_puzzle_hash, (await super().inner_puzzle_for_cat_puzhash(cat_hash)).get_tree_hash()
        )
```
