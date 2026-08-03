# Q3146: Pending-State Replay With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `create_asset` replay one pending substrate-side token lifecycle after only part of its state was consumed so `the pending token-transfer state` becomes inconsistent with `one consistent pending record across send, receive, and timeout`, breaking the invariant that pending-state cleanup and restoration must prevent any replay that could release more than one lifecycle allows and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/types.rs::create_asset
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Replay one pending substrate-side token lifecycle after only part of its state was consumed. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: pending-state cleanup and restoration must prevent any replay that could release more than one lifecycle allows
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Force an error mid-callback, replay the same lifecycle input, and assert no duplicate refund or receive becomes available. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
