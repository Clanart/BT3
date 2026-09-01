# Q2731: `validate_all_kickoff_finalizers_spent` and substitution of one withdrawal intent for another

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction register two withdrawals whose fields collide in whatever `validate_all_kickoff_finalizers_spent` in `core/src/operator.rs` uses as the intent's identity (outpoint, index, txid+vout encoding, endianness of `outputId`), so that serving one is recorded as serving the other and the second remains claimable?

## Target
- File/function: `core/src/operator.rs` -> `validate_all_kickoff_finalizers_spent`
- Entrypoint: two `withdraw` calls on Citrea by an unprivileged Citrea user -> `validate_all_kickoff_finalizers_spent`
- Attacker controls: the txid/vout encoding registered on Citrea for each withdrawal; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: have one payout discharge two withdrawal intents, or have a discharged intent stay claimable
- Invariant to test: the identity used to record a served withdrawal is injective over withdrawal intents
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: register colliding intents in a mocked-Citrea test and assert each is tracked separately
