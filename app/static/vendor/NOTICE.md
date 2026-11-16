# Device SDK

`device-sdk.js` is the unmodified browser SDK served by Longfor at
https://s.longfor.com/const-id/const-id.js, also present in the user-supplied
2026-10-16 capture. The SDK remains the property of its original provider.
This copy is used for device initialization against the original fixed service.
No AppSecret, captured account credentials, or captured personal device tokens
are included. The pinned SDK executes in a dedicated same-origin frame because
it requires browser storage; this is trusted frontend code, not an untrusted-code
isolation boundary. The frame's CSP restricts network access to its ticket relay,
and the relay never forwards account cookies or authorization headers.

Provider integration documentation:
https://www.dingxiang-inc.com/docs/detail/const-id
