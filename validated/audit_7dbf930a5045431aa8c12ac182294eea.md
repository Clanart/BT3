Looking at the external bug class: during a callback, an asset (NFT) is transferred to a callback address, and if `CallbackParams` are empty, the callback address can claim accrued fees. I need to find an analog where empty/missing callback context allows fund theft in Metric OMM.

Let me examine the key callback and payment paths: