# Q4952: calc-liquidation-params via liquidate-redeem: apply a transform after the gate that was supposed to boun

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the seized zToken amount that is immediately redeemed reach `calc-liquidation-params` (mainnet/contracts/market/v0-4-market.clar:739) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it chains the factor, the exponent curve, the penalty bound and the max repayable amount in one helper, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:739` -> `calc-liquidation-params`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the seized zToken amount that is immediately redeemed
- Exploit idea: `calc-liquidation-params` chains the factor, the exponent curve, the penalty bound and the max repayable amount in one helper. Reach it through `liquidate-redeem` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate-redeem` twice with the seized zToken amount that is immediately redeemed varied, and assert that the value `calc-liquidation-params` returns is identical in both runs; a divergence confirms the finding.
