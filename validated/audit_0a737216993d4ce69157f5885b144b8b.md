### Title
Coins sent directly to the well-known `OFFER_MOD_HASH` settlement puzzle hash can be redirected by any third party, not just lost - ([File: chia/wallet/trading/offer.py])

### Summary
The Sherlock report describes `UXDController`'s bare `receive()` fallback: a user who sends ETH straight to the contract (instead of calling `mintWithEth`) gets no `UXD` back and simply loses the funds, because the contract accepts value without any accounting/minting logic tied to it. The Chia analog is `Offer.ph()` / `OFFER_MOD_HASH`, the fixed, network-wide puzzle hash of the `settlement_payments` puzzle used for offers/trades [1](#0-0) . Any coin sent to this puzzle hash outside of an atomically-composed offer bundle sits unprotected: the puzzle imposes no signature or ownership check and will execute whatever `notarized_payments` (arbitrary destination puzzle hash / amount pairs) are supplied in the *solution* at spend time, not something committed by the sender. This is strictly worse than the original bug class — instead of merely being unminted/stuck, the funds can be actively stolen by any third party who notices the coin.

### Finding Description
`TradeManager`/`Offer` construct trades by having a wallet generate a signed transaction that sends the offered asset to `Offer.ph()` (`OFFER_MOD_HASH`) as part of a spend bundle that simultaneously creates and asserts a coin/puzzle announcement tying that output to a specific `NotarizedPayment` (a nonce plus destination puzzle hash/amount) [2](#0-1) [3](#0-2) . Safety comes entirely from atomicity: the maker's spend that creates the `OFFER_MOD_HASH` coin and the taker's completion spend that consumes it are pushed together in one `SpendBundle`, so the coin is never actually confirmed on-chain in an "unspent, waiting" state under the correct flow.

The `settlement_payments` puzzle itself (`OFFER_MOD`, loaded from `SETTLEMENT_PAYMENT`) has no owner/signature requirement — `to_valid_spend()` shows any solution can be run against `OFFER_MOD` to produce whatever `CREATE_COIN` outputs the spender chooses, gated only by matching an announcement the *original* spend bundle happened to assert [4](#0-3) . Nothing in the coin itself (parent, puzzle hash, amount) restricts who is allowed to build that solution.

Consequently, if any coin is ever confirmed on-chain sitting at `OFFER_MOD_HASH` without being consumed in the same atomic bundle that created it — e.g., a user manually constructs a payment to `Offer.ph()` thinking of it as a deposit/escrow address (directly analogous to sending ETH straight into `UXDController` instead of calling `mintWithEth`), or an offer's maker-side spend is broadcast/confirmed separately from the taker completion — that coin becomes spendable by *anyone* who observes it, who can supply their own `notarized_payments` solution and redirect the value to their own puzzle hash.

### Impact Explanation
This is not merely fund loss (as in the original ETH report) but unauthorized/unsigned coin movement: an attacker who notices a confirmed, unspent coin at `OFFER_MOD_HASH` can steal the entire amount by submitting a spend bundle with an arbitrary `CREATE_COIN` destination, since `OFFER_MOD` performs no ownership check. This satisfies "concrete unsigned or unauthorized coin movement."

### Likelihood Explanation
`OFFER_MOD_HASH` is a fixed, public constant reused for every offer in the network [1](#0-0) , so it is trivially discoverable by any spend-bundle submitter watching the mempool/chain. The likelihood hinges on a coin actually landing there outside the atomic maker+taker bundle (user error sending directly to the address, or any non-atomic broadcast of the maker-only half of an offer) — the same "user sends to the wrong/unprotected address" behavior class flagged in the original report.

### Recommendation
- Ensure wallet RPCs/UI never expose `Offer.ph()` as a normal "receiving address" a user could send funds to directly, and warn/reject standard `send_transaction` calls whose destination puzzle hash equals `OFFER_MOD_HASH`.
- Ensure the offer-creation code path never allows the maker-side spend (that creates the `OFFER_MOD_HASH` coin) to be broadcast/confirmed independently of the atomic completion spend bundle produced by `to_valid_spend()` [5](#0-4) .
- Document explicitly that any coin confirmed at `OFFER_MOD_HASH` outside a fully atomic offer/take bundle is unauthenticated and can be spent by anyone.

### Proof of Concept
1. A wallet user (or a script) constructs and broadcasts a standard transaction sending XCH/CAT to puzzle hash `Offer.ph()` (`OFFER_MOD_HASH`), believing it to be a valid deposit/escrow address (mirroring sending ETH straight to `UXDController`).
2. The transaction confirms; a coin with `puzzle_hash == OFFER_MOD_HASH` now exists on-chain, created outside any atomic maker/taker offer bundle.
3. Any third party builds a `CoinSpend` for that coin using `OFFER_MOD` with a solution containing an arbitrary `notarized_payments` list whose `CreateCoin` output points to their own puzzle hash, exactly as `Offer.to_valid_spend()` does internally [6](#0-5) .
4. This spend bundle is valid (no signature required by `OFFER_MOD`) and the attacker's spend claims the full coin value, leaving the original sender with nothing — no accounting, no minting, and no recourse, just like the reported ETH-loss issue, but with active theft rather than mere loss.

### Citations

**File:** chia/wallet/trading/offer.py (L49-50)
```python
OFFER_MOD = Program.from_bytes(SETTLEMENT_PAYMENT)
OFFER_MOD_HASH = bytes32(SETTLEMENT_PAYMENT_HASH)
```

**File:** chia/wallet/trading/offer.py (L112-128)
```python
    @staticmethod
    def notarize_payments(
        requested_payments: dict[bytes32 | None, list[CreateCoin]],  # `None` means you are requesting XCH
        coins: list[Coin],
    ) -> dict[bytes32 | None, list[NotarizedPayment]]:
        # This sort should be reproducible in CLVM with `>s`
        sorted_coins: list[Coin] = sorted(coins, key=Coin.name)
        sorted_coin_list: list[list[bytes32 | uint64]] = [coin_as_list(c) for c in sorted_coins]
        nonce: bytes32 = Program.to(sorted_coin_list).get_tree_hash()

        notarized_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        for asset_id, payments in requested_payments.items():
            notarized_payments[asset_id] = []
            for p in payments:
                notarized_payments[asset_id].append(NotarizedPayment(p.puzzle_hash, p.amount, p.memos, nonce=nonce))

        return notarized_payments
```

**File:** chia/wallet/trading/offer.py (L504-595)
```python
    # A "valid" spend means that this bundle can be pushed to the network and will succeed
    # This differs from the `to_spend_bundle` method which deliberately creates an invalid SpendBundle
    def to_valid_spend(self, arbitrage_ph: bytes32 | None = None, solver: Solver = Solver({})) -> WalletSpendBundle:
        if not self.is_valid():
            raise ValueError("Offer is currently incomplete")

        completion_spends: list[CoinSpend] = []
        all_offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        total_arbitrage_amount: dict[bytes32 | None, int] = self.arbitrage()
        for asset_id, payments in self.requested_payments.items():
            offered_coins: list[Coin] = all_offered_coins[asset_id]

            # Because of CAT supply laws, we must specify a place for the leftovers to go
            arbitrage_amount: int = total_arbitrage_amount[asset_id]
            all_payments: list[NotarizedPayment] = payments.copy()
            if arbitrage_amount > 0:
                assert arbitrage_amount is not None
                assert arbitrage_ph is not None
                all_payments.append(NotarizedPayment(arbitrage_ph, uint64(arbitrage_amount)))

            # Some assets need to know about siblings so we need to collect all spends first to be able to use them
            coin_to_spend_dict: dict[Coin, CoinSpend] = {}
            coin_to_solution_dict: dict[Coin, Program] = {}
            for coin in offered_coins:
                parent_spend: CoinSpend = next(
                    filter(lambda cs: cs.coin.name() == coin.parent_coin_info, self._bundle.coin_spends)
                )
                coin_to_spend_dict[coin] = parent_spend

                inner_solutions = []
                if coin == offered_coins[0]:
                    nonces: list[bytes32] = [p.nonce for p in all_payments]
                    for nonce in list(dict.fromkeys(nonces)):  # dedup without messing with order
                        nonce_payments: list[NotarizedPayment] = list(filter(lambda p: p.nonce == nonce, all_payments))
                        inner_solutions.append((nonce, [np.as_condition_args() for np in nonce_payments]))
                coin_to_solution_dict[coin] = Program.to(inner_solutions)

            for coin in offered_coins:
                if asset_id:
                    siblings: str = "("
                    sibling_spends: str = "("
                    sibling_puzzles: str = "("
                    sibling_solutions: str = "("
                    disassembled_offer_mod: str = disassemble(OFFER_MOD)
                    for sibling_coin in offered_coins:
                        if sibling_coin != coin:
                            siblings += (
                                "0x"
                                + sibling_coin.parent_coin_info.hex()
                                + sibling_coin.puzzle_hash.hex()
                                + uint64(sibling_coin.amount).stream_to_bytes().hex()
                                + " "
                            )
                            sibling_spends += "0x" + bytes(coin_to_spend_dict[sibling_coin]).hex() + " "
                            sibling_puzzles += disassembled_offer_mod + " "
                            sibling_solutions += disassemble(coin_to_solution_dict[sibling_coin]) + " "
                    siblings += ")"
                    sibling_spends += ")"
                    sibling_puzzles += ")"
                    sibling_solutions += ")"

                    solution: Program = solve_puzzle(
                        self.driver_dict[asset_id],
                        Solver(
                            {
                                "coin": "0x"
                                + coin.parent_coin_info.hex()
                                + coin.puzzle_hash.hex()
                                + uint64(coin.amount).stream_to_bytes().hex(),
                                "parent_spend": "0x" + bytes(coin_to_spend_dict[coin]).hex(),
                                "siblings": siblings,
                                "sibling_spends": sibling_spends,
                                "sibling_puzzles": sibling_puzzles,
                                "sibling_solutions": sibling_solutions,
                                **solver.info,
                            }
                        ),
                        OFFER_MOD,
                        Program.to(coin_to_solution_dict[coin]),
                    )
                else:
                    solution = Program.to(coin_to_solution_dict[coin])

                completion_spends.append(
                    make_spend(
                        coin,
                        construct_puzzle(self.driver_dict[asset_id], OFFER_MOD) if asset_id else OFFER_MOD,
                        solution,
                    )
                )

        return WalletSpendBundle.aggregate([WalletSpendBundle(completion_spends, G2Element()), self._bundle])
```

**File:** chia/wallet/trade_manager.py (L603-649)
```python
            all_coins: list[Coin] = [c for coins in coins_to_offer.values() for c in coins]
            notarized_payments: dict[bytes32 | None, list[NotarizedPayment]] = Offer.notarize_payments(
                requested_payments, all_coins
            )
            announcements_to_assert = Offer.calculate_announcements(notarized_payments, driver_dict)

            all_transactions: list[TransactionRecord] = []
            fee_left_to_pay: uint64 = fee
            # The access of the sorted keys here makes sure we create the XCH transaction first to make sure we pay fee
            # with the XCH side of the offer and don't create an extra fee transaction in other wallets.
            for id in sorted(coins_to_offer.keys(), key=lambda id: id != 1):
                selected_coins = coins_to_offer[id]
                if isinstance(id, int):
                    wallet = self.wallet_state_manager.wallets.get(uint32(id))
                else:
                    wallet = await self.wallet_state_manager.get_wallet_for_asset_id(id)
                async with self.wallet_state_manager.new_action_scope(
                    action_scope.config.tx_config, push=False
                ) as inner_action_scope:
                    # This should probably not switch on whether or not we're spending XCH but it has to for now
                    assert wallet is not None
                    if wallet.type() == WalletType.NFT:
                        assert isinstance(wallet, NFTWallet)
                        # This is to generate the tx for specific nft assets, i.e. not using
                        # wallet_id as the selector which would select any coins from nft_wallet
                        amounts = [coin.amount for coin in selected_coins]
                        await wallet.generate_signed_transaction(
                            # [abs(offer_dict[id])],
                            amounts,
                            [Offer.ph()],
                            inner_action_scope,
                            fee=fee_left_to_pay,
                            coins=selected_coins,
                            extra_conditions=(*extra_conditions, *announcements_to_assert),
                        )
                    else:
                        # ATTENTION: new_wallets
                        assert isinstance(wallet, (Wallet, CATWallet, DataLayerWallet))
                        await wallet.generate_signed_transaction(
                            [uint64(abs(offer_dict[id]))],
                            [Offer.ph()],
                            inner_action_scope,
                            fee=fee_left_to_pay,
                            coins=selected_coins,
                            extra_conditions=(*extra_conditions, *announcements_to_assert),
                            add_authorizations_to_cr_cats=False,
                        )
```
